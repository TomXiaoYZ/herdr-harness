#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Bash): environments are borrowed, so they are created only through the
doors that record a return condition.

Denies, with the sanctioned alternative in the message:
- `git worktree add …` typed by hand. Delegated tasks use `task_worktree.py new` (ledger, cap, sparse checkout);
  Repositories Claude Code develops itself use the desktop app's worktree session or EnterWorktree. A command that itself runs
  task_worktree.py passes.
- `docker run -d/--detach …` without `--label agent.task=<task>` and `docker compose … up` without a project
  name (`-p`/`--project-name` or `COMPOSE_PROJECT_NAME=` in the command). Without an identity nothing can
  tear the containers down later; the Stop hook and workspace_hygiene.py look for exactly these markers.
Everything else passes. Any internal error passes (exit 0): this guard never blocks work by accident.
"""
import json
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get('tool_name') != 'Bash':
        return 0
    cmd = (data.get('tool_input') or {}).get('command') or ''
    if not cmd:
        return 0
    # Judge commands, not prose: drop heredoc bodies (docs, commit messages), quoted strings and backtick spans
    # before matching, so writing about these commands is not mistaken for running them.
    cmd = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\1(\n|$)", ' ', cmd, flags=re.S)
    cmd = re.sub(r'`[^`\n]*`', ' ', cmd)
    cmd = re.sub(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'', ' ', cmd)
    flat = ' '.join(cmd.split())
    reasons = []
    if re.search(r'\bgit\b[^|;&\n]*\bworktree\s+add\b', flat) and 'task_worktree.py' not in flat:
        reasons.append('不要手敲 `git worktree add`：派 Codex 的任务用 `~/.claude/skills/delegate-to-codex/scripts/task_worktree.py new '
                       '--repo <仓库根> --task <task>`（登账本、上限 8、稀疏检出）；Claude Code 自己开发的仓库用桌面 App 的工作树会话或 EnterWorktree。'
                       '每个 worktree 都要有归还条件（2026-11-10 约定）。')
    for m in re.finditer(r'\bdocker\s+run\b([^|;&\n]*)', flat):
        args = m.group(1)
        if re.search(r'(^|\s)(-d|--detach)(\s|$)', args) and 'agent.task=' not in args:
            reasons.append('后台容器必须带身份：`docker run -d --label agent.task=<task> …`，任务结束前 `docker rm -f` 同标签容器。'
                           '没有标签的容器没人能收。')
            break
    for m in re.finditer(r'\bdocker\s+compose\b([^|;&\n]*)\bup\b', flat):
        args = m.group(1)
        if not re.search(r'(^|\s)(-p|--project-name)(\s|=)', args) and 'COMPOSE_PROJECT_NAME=' not in flat:
            reasons.append('`docker compose up` 必须带项目名：`docker compose -p <task> … up`，收口时 `docker compose -p <task> down -v`。')
            break
    if not reasons:
        return 0
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': ' '.join(reasons)}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
