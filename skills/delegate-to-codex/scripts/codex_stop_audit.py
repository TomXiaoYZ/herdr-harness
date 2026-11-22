#!/usr/bin/env python3
"""Codex Stop hook: let a delegated thread fix budget and defensive-code findings before its turn ends.

Installed in ~/.codex/hooks.json (event "Stop"). Only threads that `codex_thread.py start` registered under
~/.agents/work-plans/evidence/delegate/_threads/<thread id>.json are audited; every other Codex thread
(the user's own sessions) passes through untouched. Runs diff_audit.py for the registered worktree/card/base;
when it reports over-budget or outside-list files, or defensive hits, the audit text goes to stderr and
exit 2 sends Codex back to work. At most MAX_BLOCKS blocks per thread, so a disagreement ends in the receipt
(Codex must then list the remaining hits under omitted_guards) instead of a loop.
Since 2026-11-10 it also blocks while containers labelled agent.task=<task> (or a compose project named <task>) exist. Any internal error exits 0.
"""
import json
import os
import subprocess
import sys

REG_DIR = os.path.expanduser('~/.agents/work-plans/evidence/delegate/_threads')
AUDIT = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'diff_audit.py')
MAX_BLOCKS = 2


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    tid = os.environ.get('CODEX_THREAD_ID') or data.get('session_id') or ''
    try:
        # One line per invocation, so a probe can tell "hook never ran" from "hook ran and passed".
        os.makedirs(REG_DIR, exist_ok=True)
        with open(os.path.join(REG_DIR, 'stop-hook.log'), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'tid': tid, 'event': data.get('hook_event_name'), 'cwd': data.get('cwd'),
                                 'stop_hook_active': data.get('stop_hook_active')}, ensure_ascii=False) + '\n')
    except Exception:
        pass
    reg_path = os.path.join(REG_DIR, f'{tid}.json')
    if not tid or not os.path.exists(reg_path):
        return 0
    try:
        reg = json.load(open(reg_path, encoding='utf-8'))
    except Exception:
        return 0
    if reg.get('blocks', 0) >= MAX_BLOCKS:
        return 0
    try:
        res = subprocess.run([sys.executable, '-I', AUDIT, '--worktree', reg['worktree'], '--base', reg['base'],
                              '--card', reg['card']], capture_output=True, text=True, timeout=120)
    except Exception:
        return 0
    hits = '防御性代码命中（0 处）' not in res.stdout
    leftovers = container_leftovers(os.path.basename(reg['worktree'].rstrip('/')))
    if res.returncode == 0 and not hits and not leftovers:
        return 0
    reg['blocks'] = reg.get('blocks', 0) + 1
    try:
        json.dump(reg, open(reg_path, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass
    sys.stderr.write(
        f'回合末自检（第 {reg["blocks"]}/{MAX_BLOCKS} 次）未通过，先按下面的审计结果修改再结束：\n'
        '超预算或清单外的文件要删掉或并回预算；每处防御性代码命中都要对应任务卡“编码要求”列出的边界，'
        '对不上的删除；确实需要但卡里没列的，不要加，写进回执 omitted_guards。\n'
        + (f'还有本任务起的 Docker 对象没有收掉（标签 agent.task={os.path.basename(reg["worktree"])} 或同名 compose 项目）：'
           f'{", ".join(leftovers)}。按卡“完成定义”执行 down -v / rm 后再结束。\n' if leftovers else '')
        + '\n' + res.stdout[:6000])
    return 2


def container_leftovers(task):
    """Containers this task started and did not tear down: label agent.task=<task> or compose project <task>.
    The card's 完成定义 requires `down -v`; an environment outlives its task only by the user's decision."""
    if not task:
        return []
    names = []
    for flt in (f'label=agent.task={task}', f'label=com.docker.compose.project={task}'):
        try:
            r = subprocess.run(['docker', 'ps', '-a', '--filter', flt, '--format', '{{.Names}}'],
                               capture_output=True, text=True, timeout=20)
        except Exception:
            return []
        if r.returncode == 0:
            names += [n for n in r.stdout.split() if n]
    return sorted(set(names))


if __name__ == '__main__':
    sys.exit(main())
