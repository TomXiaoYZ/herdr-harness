"""hc-task 把 Claude 桌面版的定时任务搬到 launchd + herdr 常驻会话。守住：cron 换成 launchd 时间表不丢不多、
导入时暂停的保持暂停、launchd 任务只在启用时存在、运行记录只留最近 KEEP_RUNS 条且汇总只列最新一次、结果取转录里最后一条助手文字。"""
import importlib.machinery
import importlib.util
import json
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ht(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HC_TASK_HOME', str(tmp_path / 'tasks'))
    monkeypatch.setenv('HC_TASK_LAUNCHAGENTS', str(tmp_path / 'LaunchAgents'))
    path = str(ROOT / 'herdr' / 'bin' / 'hc-task')
    spec = importlib.util.spec_from_loader('hc_task', importlib.machinery.SourceFileLoader('hc_task', path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(mod, 'launchctl', lambda *a: calls.append(a) or type('R', (), {'returncode': 0, 'stderr': ''})())
    return mod, tmp_path, calls


def test_cron_to_calendar(ht) -> None:
    mod, _, _ = ht
    assert mod.calendar('0 9 * * *') == [{'Minute': 0, 'Hour': 9}]
    assert mod.calendar('30 9 * * 1,4') == [{'Minute': 30, 'Hour': 9, 'Weekday': 1}, {'Minute': 30, 'Hour': 9, 'Weekday': 4}]
    assert len(mod.calendar('5,35 9-15 * * 1-5')) == 2 * 7 * 5
    assert len(mod.calendar('10,40 0-9,16-23 * * 1-5')) == 2 * 18 * 5
    assert mod.calendar('0 0 * * 7') == [{'Minute': 0, 'Hour': 0, 'Weekday': 0}]
    with pytest.raises(ValueError):
        mod.calendar('0 9 1 * *')


def test_import_keeps_paused_and_strips_frontmatter(ht) -> None:
    mod, home, _ = ht
    app = home / 'Library' / 'Application Support' / 'Claude'
    org = app / 'claude-code-sessions' / 'acct' / 'org'
    org.mkdir(parents=True)
    (app / 'config.json').write_text(json.dumps({'lastKnownAccountUuid': 'acct'}))
    for tid, body in {'web': '---\nname: web\n---\n打开 Chrome 看数据', 'watch': '汇总昨天的构建日志'}.items():
        d = home / '.claude' / 'scheduled-tasks' / tid
        d.mkdir(parents=True)
        (d / 'SKILL.md').write_text(body)
    (org / 'scheduled-tasks.json').write_text(json.dumps({'scheduledTasks': [
        {'id': 'web', 'displayName': '网页', 'cronExpression': '0 9 * * *', 'enabled': True, 'cwd': '/w', 'permissionMode': 'auto'},
        {'id': 'watch', 'cronExpression': '5,35 9-15 * * 1-5', 'enabled': False, 'cwd': '/m'},
        {'id': 'once', 'fireAt': 1, 'enabled': False},
    ]}))
    mod.cmd_import(apply=False)
    assert not (home / 'tasks' / 'web').exists()  # 不带 --apply 只列计划
    mod.cmd_import(apply=True)
    web, watch = (json.loads((home / 'tasks' / t / 'task.json').read_text()) for t in ('web', 'watch'))
    assert (web['enabled'], web['chrome'], web['name'], web['permission_mode']) == (True, True, '网页', 'auto')
    assert (watch['enabled'], watch['chrome']) == (False, False)
    assert web['session'] != watch['session']
    assert (home / 'tasks' / 'web' / 'prompt.md').read_text() == '打开 Chrome 看数据'
    assert not (home / 'tasks' / 'once').exists()
    before = web['session']
    mod.cmd_import(apply=True)  # 再导一次不覆盖，会话 id 不变
    assert json.loads((home / 'tasks' / 'web' / 'task.json').read_text())['session'] == before


def test_launchd_job_only_when_enabled(ht) -> None:
    mod, home, calls = ht
    t = {'id': 'x', 'cron': '30 9 * * 1,4', 'enabled': True}
    (home / 'tasks' / 'x').mkdir(parents=True)
    assert mod.sync_one(t) == 'on'
    plist = plistlib.loads((home / 'LaunchAgents' / 'local.hc-task.x.plist').read_bytes())
    assert plist['ProgramArguments'][-2:] == ['run', 'x'] and len(plist['StartCalendarInterval']) == 2
    assert plist['EnvironmentVariables']['HC_TASK_HOME'] == str(home / 'tasks')
    assert calls[-1][0] == 'bootstrap'
    assert mod.sync_one({**t, 'enabled': False}) == 'off'
    assert not (home / 'LaunchAgents' / 'local.hc-task.x.plist').exists()


def test_runs_trimmed_and_results_show_latest(ht) -> None:
    mod, home, _ = ht
    d = home / 'tasks' / 'x'
    d.mkdir(parents=True)
    (d / 'task.json').write_text(json.dumps({'id': 'x', 'name': '某任务', 'cron': '0 9 * * *', 'enabled': True}))
    for i in range(mod.KEEP_RUNS + 5):
        mod.record('x', {'started': f'2026-11-{1 + i // 24:02d}T{i % 24:02d}:00:00-05:00', 'status': 'ok', 'result': f'第 {i} 次'})
    runs = [p for p in (d / 'runs').glob('*.json') if p.name != 'latest.json']
    assert len(runs) == mod.KEEP_RUNS
    text = (home / 'tasks' / 'RESULTS.md').read_text()
    assert f'第 {mod.KEEP_RUNS + 4} 次' in text and '第 0 次' not in text


def test_last_text_reads_after_offset(ht, tmp_path) -> None:
    mod, _, _ = ht
    log = tmp_path / 's.jsonl'
    old = json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '上一次'}]}}) + '\n'
    log.write_text(old + '\n'.join(json.dumps(x) for x in [
        {'type': 'user', 'message': {'content': 'go'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '中间'}, {'type': 'tool_use', 'name': 'Bash'}]}},
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash'}]}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '本次结果：好了'}]}},
    ]) + '\n')
    assert mod.last_text(str(log), len(old.encode())) == '本次结果：好了'
    assert mod.last_text(str(log), log.stat().st_size) is None


def test_save_creates_validates_and_edits(ht, capsys, monkeypatch) -> None:
    mod, home, calls = ht
    kills = []
    monkeypatch.setattr(mod, 'terminal', lambda t: {'state': 'live', 'status': 'idle'})
    monkeypatch.setattr(mod, 'hc', lambda *a, **k: kills.append(a) or type('R', (), {'stdout': '{"state": "finished"}'})())
    folder = home / 'proj'
    folder.mkdir()
    base = {'name': 'Morning triage!', 'cron': '0  9 * * 1-5', 'cwd': str(folder), 'prompt': 'look at the inbox', 'enabled': True}
    for bad, why in (({'name': ' '}, 'name'), ({'prompt': ''}, 'prompt'), ({'cron': '0 9 1 * *'}, 'schedule'),
                     ({'cwd': str(home / 'nope')}, 'folder'), ({'permission_mode': 'yolo'}, 'permission')):
        with pytest.raises(SystemExit):
            mod.cmd_save({**base, **bad})
        assert why in capsys.readouterr().err
    mod.cmd_save(base)
    tid = json.loads(capsys.readouterr().out)['id']
    assert tid == 'morning-triage'
    t = json.loads((home / 'tasks' / tid / 'task.json').read_text())
    assert (t['cron'], t['permission_mode'], t['model'], t['chrome']) == ('0 9 * * 1-5', 'auto', None, False)
    assert (home / 'tasks' / tid / 'prompt.md').read_text() == 'look at the inbox\n'
    assert (home / 'LaunchAgents' / f'local.hc-task.{tid}.plist').exists()
    mod.cmd_save(base)  # 同名再建一个：id 加后缀，不覆盖
    assert json.loads(capsys.readouterr().out)['id'] == 'morning-triage-2'

    session = t['session']
    mod.cmd_save({**base, 'id': tid, 'prompt': 'new words', 'enabled': False})  # 只改提示词和开关：会话不动、不重启
    out = json.loads(capsys.readouterr().out)
    t = json.loads((home / 'tasks' / tid / 'task.json').read_text())
    assert (t['session'], t['enabled'], out['note'], kills) == (session, False, None, [])
    assert not (home / 'LaunchAgents' / f'local.hc-task.{tid}.plist').exists()
    other = home / 'other'
    other.mkdir()
    mod.cmd_save({**base, 'id': tid, 'cwd': str(other), 'model': 'opus'})  # 换目录：新会话，空闲的终端被关掉
    out = json.loads(capsys.readouterr().out)
    t = json.loads((home / 'tasks' / tid / 'task.json').read_text())
    assert t['session'] != session and t['model'] == 'opus' and out['note']
    assert [c for c in kills if c[0] != 'get'] == [('kill', f'sched-{tid}'), ('rm', f'sched-{tid}')]


def test_next_runs_and_errors(ht, capsys) -> None:
    mod, _, _ = ht
    runs = mod.next_runs('0 9 * * 1-5', 5)
    assert len(runs) == 5 and all('T09:00' in r for r in runs)
    from datetime import datetime
    assert all(datetime.fromisoformat(r).weekday() < 5 for r in runs)
    mod.cmd_next('0 9 *')
    assert 'five fields' in json.loads(capsys.readouterr().out)['error']
    mod.cmd_next('0 25 * * *')
    assert '25' in json.loads(capsys.readouterr().out)['error']


def test_running_follows_lock(ht) -> None:
    import fcntl
    mod, home, _ = ht
    (home / 'tasks' / 'x' / 'runs').mkdir(parents=True)
    assert mod.running('x') is False
    with open(home / 'tasks' / 'x' / 'runs' / '.lock', 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert mod.running('x') is True
    assert mod.running('x') is False and mod.running('missing') is False
