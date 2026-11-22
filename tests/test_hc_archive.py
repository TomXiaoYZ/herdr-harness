"""hc-archive 删工作树、关 PR，所以它不该动的东西要有测试守着：主检出、会话外还在用的工作树、
会话自己的 PR、未提交的改动（先快照）。用临时仓库加假的 hc / gh 跑真实脚本；HOME 指到临时目录，
台账和废纸篓都落在里面。"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_HC = """#!/usr/bin/env python3
import os, sys
state = os.environ['FAKE_STATE']
if sys.argv[1] == 'ls':
    print(open(os.path.join(state, 'terminals.json')).read())
else:
    open(os.path.join(state, 'hc.log'), 'a').write(' '.join(sys.argv[1:]) + '\\n')
"""

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
state = os.environ['FAKE_STATE']
if sys.argv[1:3] == ['pr', 'list']:
    number = json.load(open(os.path.join(state, 'prs.json'))).get(sys.argv[sys.argv.index('--head') + 1])
    print(json.dumps([{'number': number, 'url': f'https://example.invalid/pr/{number}'}] if number else []))
else:
    open(os.path.join(state, 'gh.log'), 'a').write(' '.join(sys.argv[1:4]) + '\\n')
"""


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def session(tmp_path, monkeypatch):
    """母会话 t1 在自己的工作树里；下面有四个子 agent 和一个孙 agent，另有一个会话外的终端 t9。"""
    tmp = Path(os.path.realpath(tmp_path))
    home, state, repo = tmp / 'home', tmp / 'state', tmp / 'repo'
    for d in (home, state, repo):
        d.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('FAKE_STATE', str(state))
    for key, value in {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
                       'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}.items():
        monkeypatch.setenv(key, value)
    git(repo, 'init', '-b', 'main')
    (repo / 'a.txt').write_text('a\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'init')
    trees = {}
    for name in ('own', 'kid', 'dirty', 'shared'):
        trees[name] = repo / '.worktrees' / name
        git(repo, 'worktree', 'add', '-b', f'task/{name}', str(trees[name]))
    (trees['dirty'] / 'a.txt').write_text('changed\n')
    (trees['dirty'] / 'new.txt').write_text('untracked\n')

    def term(address, name, cwd, parent=None):
        return {'address': address, 'name': name, 'cwd': str(cwd), 'parent': parent, 'state': 'live'}

    (state / 'terminals.json').write_text(json.dumps({'terminals': [
        term('t1', 'mother', trees['own']),
        term('t2', 'kid', trees['kid'], 't1'),
        term('t3', 'in-main', repo, 't1'),
        term('t4', 'in-shared', trees['shared'], 't1'),
        term('t5', 'grandkid', trees['dirty'], 't2'),
        term('t9', 'someone-else', trees['shared']),
        {'address': 't8', 'name': 'ended', 'cwd': str(trees['kid']), 'parent': 't1', 'state': 'exited'},
    ]}))
    (state / 'prs.json').write_text(json.dumps({'task/own': 1, 'task/kid': 2, 'task/shared': 3}))
    for name, body in (('hc', FAKE_HC), ('gh', FAKE_GH)):
        (state / name).write_text(body)
        (state / name).chmod(0o755)
    path = str(ROOT / 'herdr' / 'bin' / 'hc-archive')
    spec = importlib.util.spec_from_loader('hc_archive', importlib.machinery.SourceFileLoader('hc_archive', path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'HC', str(state / 'hc'))
    monkeypatch.setattr(mod, 'GH', str(state / 'gh'))

    def log(name):
        f = state / f'{name}.log'
        return f.read_text().splitlines() if f.exists() else []

    return mod, repo, trees, home, log


def by_branch(plan):
    return {w['branch']: w for w in plan['worktrees']}


def test_plan_changes_nothing_and_marks_what_is_spared(session) -> None:
    mod, repo, trees, home, log = session

    plan = mod.plan('mother')

    assert [t['address'] for t in plan['terminals']][0] == 't5'  # 最深的先结束
    assert {t['address'] for t in plan['terminals']} == {'t2', 't3', 't4', 't5'}  # 不含母会话、会话外和已结束的
    trees_planned = by_branch(plan)
    assert set(trees_planned) == {'task/own', 'task/kid', 'task/dirty', 'task/shared'}  # 主检出不在其中
    assert {b for b, w in trees_planned.items() if w['close_pr']} == {'task/kid'}
    assert trees_planned['task/own']['own'] and trees_planned['task/own']['pr']['number'] == 1
    assert trees_planned['task/shared']['shared']
    assert trees_planned['task/dirty']['dirty'] and not trees_planned['task/kid']['dirty']
    assert all(t.is_dir() for t in trees.values())
    assert log('hc') == [] and log('gh') == []


def test_archive_returns_only_what_the_session_borrowed(session) -> None:
    mod, repo, trees, home, log = session

    done = by_branch(mod.archive(mod.plan('t1')))

    assert log('gh') == ['pr close 2']  # 会话自己的 PR、共用工作树的 PR 都不关
    assert log('hc')[:2] == ['kill t5', 'rm t5']
    assert not any(line.endswith((' t1', ' t9')) for line in log('hc'))
    assert (repo / 'a.txt').read_text() == 'a\n'
    assert trees['shared'].is_dir() and done['task/shared']['result'].startswith('kept')
    assert not trees['own'].exists() and not trees['kid'].exists() and not trees['dirty'].exists()
    assert done['task/kid']['result'] == 'returned'

    snapshots = list((home / '.Trash' / 'agent-worktrees').iterdir())
    assert len(snapshots) == 1
    assert 'changed' in (snapshots[0] / 'tracked-changes.patch').read_text()
    assert (snapshots[0] / 'untracked.tar.gz').is_file()
    assert json.loads((snapshots[0] / 'MANIFEST.json').read_text())['branch'] == 'task/dirty'
    branches = subprocess.run(['git', 'branch', '--list', 'task/dirty'], cwd=repo, capture_output=True, text=True).stdout
    assert 'task/dirty' in branches  # 有改动的分支留着，可按清单恢复


def test_unknown_session_is_refused(session) -> None:
    mod = session[0]

    with pytest.raises(SystemExit):
        mod.plan('nope')
