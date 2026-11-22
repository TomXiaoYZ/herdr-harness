"""hc-schedules 给侧栏“定时任务”一栏供数：哪些任务在跑、下次什么时候跑。用临时 HOME 里的假清单跑真实脚本的函数，
守住三件事：暂停的不算在跑、跑过的一次性任务和旧账户的任务不出现、下次运行时间按 cron 算对。"""
import importlib.machinery
import importlib.util
import json
import os
import plistlib
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 20, 2, 30).timestamp()  # 周五凌晨，本地时间


def at(*args):
    return int(datetime(*args).timestamp())


@pytest.fixture
def hc(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    path = str(ROOT / 'herdr' / 'bin' / 'hc-schedules')
    spec = importlib.util.spec_from_loader('hc_schedules', importlib.machinery.SourceFileLoader('hc_schedules', path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp_path


def desktop(home, account, tasks, current=True):
    root = home / 'Library' / 'Application Support' / 'Claude'
    d = root / 'claude-code-sessions' / account / 'org'
    d.mkdir(parents=True)
    (d / 'scheduled-tasks.json').write_text(json.dumps({'scheduledTasks': tasks}))
    if current:
        (root / 'config.json').write_text(json.dumps({'lastKnownAccountUuid': account}))


def test_next_slot_follows_cron(hc) -> None:
    mod, _ = hc
    assert mod.slot(['5,35 9-15 * * 1-5'], NOW, True) == at(2026, 11, 20, 9, 5)
    assert mod.slot(['30 9 * * 1,4'], NOW, True) == at(2026, 11, 23, 9, 30)  # 下周一
    assert mod.slot(['0 20 * * 0', '30 23 * * 5'], NOW, True) == at(2026, 11, 20, 23, 30)  # 多条取最近
    assert mod.slot(['0 9 * * *'], NOW, False) == at(2026, 11, 19, 9, 0)
    assert mod.slot(['0 0 1 * 1'], NOW, True) == at(2026, 11, 23, 0, 0)  # 日和星期都写：满足一个就跑
    assert mod.slot(['bogus'], NOW, True) is None


def test_desktop_tasks_on_paused_and_left_out(hc) -> None:
    mod, home = hc
    ran = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(at(2026, 11, 19, 9, 2)))
    desktop(home, 'old', [{'id': 'stale', 'cronExpression': '0 4 * * *', 'enabled': True}], current=False)
    desktop(home, 'now', [
        {'id': 'daily', 'displayName': '每日', 'cronExpression': '0 9 * * *', 'enabled': True, 'lastRunAt': ran, 'createdAt': 1},
        {'id': 'watch', 'displayName': '盘中盯盘', 'cronExpression': '5,35 9-15 * * 1-5', 'enabled': False, 'lastRunAt': ran},
        {'id': 'missed', 'cronExpression': '0 1 * * *', 'enabled': True, 'lastRunAt': ran, 'createdAt': 1},
        {'id': 'fired', 'fireAt': (NOW - 3600) * 1000, 'enabled': False},
        {'id': 'once', 'fireAt': (NOW + 3600) * 1000, 'enabled': True},
    ])
    got = {k['id']: k for k in mod.collect(NOW)}
    assert set(got) == {'daily', 'watch', 'missed', 'once'}
    assert (got['daily']['state'], got['daily']['next'], got['daily']['problem']) == ('on', at(2026, 11, 20, 9, 0), None)
    assert (got['watch']['state'], got['watch']['next']) == ('off', None)
    assert got['missed']['problem'] and got['missed']['next'] == at(2026, 11, 21, 1, 0)
    assert got['once']['next'] == int(NOW + 3600)
    assert [k['id'] for k in mod.collect(NOW)][-1] == 'watch'  # 暂停的排最后


def test_codex_and_launchd_sources(hc) -> None:
    mod, home = hc
    auto = home / '.codex' / 'automations' / 'a1'
    auto.mkdir(parents=True)
    (auto / 'automation.toml').write_text('id = "a1"\nname = "追踪"\nstatus = "PAUSED"\nrrule = "FREQ=MINUTELY;INTERVAL=5"\n')
    agents = home / 'Library' / 'LaunchAgents'
    agents.mkdir(parents=True)
    for label, body in {
        'test.hc.weekly': {'ProgramArguments': ['/usr/bin/true'], 'StartCalendarInterval': {'Weekday': 1, 'Hour': 9, 'Minute': 30}},
        'test.hc.daemon': {'ProgramArguments': ['/usr/bin/true'], 'KeepAlive': True},
        'test.hc.vendor': {'ProgramArguments': ['/Applications/X.app/Contents/MacOS/x'], 'StartInterval': 3600},
    }.items():
        (agents / f'{label}.plist').write_bytes(plistlib.dumps({'Label': label, **body}))
    got = {k['id']: k for k in mod.collect(NOW)}
    assert set(got) == {'a1', 'test.hc.weekly'}  # 常驻服务和应用自带的更新器不是定时任务
    assert (got['a1']['state'], got['a1']['when']) == ('off', 'every 5 min')
    assert (got['test.hc.weekly']['state'], got['test.hc.weekly']['when']) == ('off', '30 9 * * 1')  # 没加载进 launchd


def test_session_jobs_of_live_sessions(hc) -> None:
    mod, home = hc
    made = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(NOW - 86400))

    def use(tid, name, **given):
        return json.dumps({'timestamp': made, 'message': {'content': [{'type': 'tool_use', 'id': tid, 'name': name, 'input': given}]}})

    def result(tid, jid):
        return json.dumps({'timestamp': made, 'toolUseResult': {'id': jid, 'humanSchedule': 'x', 'nextFireAt': (NOW - 60) * 1000},
                           'message': {'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': 'ok'}]}})

    log = [use('u1', 'CronCreate', cron='5,35 9-15 * * 1-5', prompt='盘中盯盘（本会话内运行）。先读', recurring=True), result('u1', 'j1'),
           use('u2', 'CronCreate', cron='0 3 * * *', prompt='删掉的', recurring=True), result('u2', 'j2'), use('u3', 'CronDelete', id='j2'),
           use('u4', 'CronCreate', cron='0 2 20 11 *', prompt='跑过的一次性', recurring=False), result('u4', 'j4')]
    (home / '.claude' / 'sessions').mkdir(parents=True)
    for sid, pid in (('live', os.getpid()), ('dead', 2 ** 22 + 12345)):
        d = home / '.claude' / 'projects' / 'p'
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{sid}.jsonl').write_text('\n'.join(log) + '\n')
        (home / '.claude' / 'sessions' / f'{pid}.json').write_text(json.dumps({'pid': pid, 'sessionId': sid, 'cwd': '/w'}))
    got = mod.collect(NOW)
    assert [(k['source'], k['name'], k['state']) for k in got] == [('session', '盘中盯盘', 'on')]  # 已删的、跑过的、进程不在的都不算
    assert got[0]['next'] == at(2026, 11, 20, 9, 5) and got[0]['expires'] == int(NOW) + 6 * 86400 and got[0]['problem'] is None
    assert mod.sessions(NOW + 7 * 86400) == []  # 循环任务 7 天到期


def test_hc_task_source_and_its_launchd_job_listed_once(hc) -> None:
    mod, home = hc

    def task(tid, enabled, run=None):
        d = home / '.agents' / 'scheduled-tasks' / tid
        (d / 'runs').mkdir(parents=True)
        (d / 'task.json').write_text(json.dumps({'id': tid, 'name': tid.upper(), 'cron': '0 9 * * *', 'cwd': '/w', 'enabled': enabled}))
        os.utime(d / 'task.json', (NOW - 30 * 86400, NOW - 30 * 86400))
        if run:
            (d / 'runs' / 'latest.json').write_text(json.dumps(run))

    iso = lambda *a: datetime(*a).astimezone().isoformat()
    task('fine', True, {'started': iso(2026, 11, 19, 9, 0, 3), 'status': 'ok', 'result': '本次结果：发了 3 条'})
    task('stuck', True, {'started': iso(2026, 11, 19, 9, 0, 3), 'status': 'blocked', 'result': '等你'})
    task('missed', True, {'started': iso(2026, 11, 17, 9, 0, 3), 'status': 'ok'})
    task('paused', False)
    agents = home / 'Library' / 'LaunchAgents'
    agents.mkdir(parents=True)
    (agents / 'local.hc-task.fine.plist').write_bytes(plistlib.dumps(
        {'Label': 'local.hc-task.fine', 'ProgramArguments': [str(home / '.local/bin/hc-task'), 'run', 'fine'], 'StartCalendarInterval': [{'Hour': 9, 'Minute': 0}]}))
    got = {k['id']: k for k in mod.collect(NOW)}
    assert set(got) == {'fine', 'stuck', 'missed', 'paused'}  # launchd 那份不重复列
    assert (got['fine']['source'], got['fine']['problem'], got['fine']['result']) == ('herdr', None, '本次结果：发了 3 条')
    assert (got['fine']['status'], got['stuck']['status']) == ('ok', 'blocked')
    assert got['fine']['open'] == ['hc-task', 'open', 'fine'] and got['fine']['next'] == at(2026, 11, 20, 9, 0)
    assert got['stuck']['problem'] == 'last run is waiting for you'
    assert 'missed' in got['missed']['problem']
    assert (got['paused']['state'], got['paused']['next'], got['paused']['problem']) == ('off', None, None)
