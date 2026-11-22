#!/usr/bin/env python3
"""Claude Code PreToolUse hook: Claude Code plans, Codex implements.

Blocks Edit/Write/MultiEdit/NotebookEdit on code files inside repositories whose implementation is delegated to
Codex. Allowed anywhere: documents (*.md, docs/), task cards, design prototypes (prototypes/, docs/designs/),
agent config (.claude/, .agents/, .codex/, AGENTS.md, CLAUDE.md), and everything outside ~/Code (scratchpads,
evidence, home config). Allowed in full: the repositories Claude Code develops itself, listed in
CC_EDIT_FULL_ACCESS (colon-separated paths; default: this harness checkout).

Reads the hook JSON on stdin; exit 2 with a message on stderr blocks the tool call. Any internal error exits 0
so a bug here never blocks work. Installed from ~/.claude/settings.json (hooks.PreToolUse).
"""
import json
import os
import re
import sys

CODE_ROOT = os.path.realpath(os.path.expanduser('~/Code'))
HARNESS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
FULL_ACCESS = [os.path.realpath(os.path.expanduser(p))
               for p in (os.environ.get('CC_EDIT_FULL_ACCESS') or HARNESS).split(':') if p]
ALLOWED = re.compile(
    r'(^|/)(docs|prototypes|\.claude|\.agents|\.codex|\.worktrees/[^/]+/docs)(/|$)'
    r'|(^|/)(AGENTS|CLAUDE|README|GLOSSARY)\.md$'
    r'|\.(md|mdx|txt|rst)$'
)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get('tool_name') not in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        return 0
    path = (data.get('tool_input') or {}).get('file_path') or (data.get('tool_input') or {}).get('notebook_path') or ''
    if not path:
        return 0
    real = os.path.realpath(os.path.expanduser(path))
    if not real.startswith(CODE_ROOT + os.sep):
        return 0
    if any(real == r or real.startswith(r + os.sep) for r in FULL_ACCESS):
        return 0
    rel = os.path.relpath(real, CODE_ROOT)
    if ALLOWED.search(rel):
        return 0
    sys.stderr.write(
        f'delegate-to-codex 钩子拒绝：Claude Code 不直接改允许清单（CC_EDIT_FULL_ACCESS）以外仓库的代码文件（{rel}）。'
        '把这处改动写进任务卡的下一轮交给 Codex；文档、任务卡、prototypes/ 和 *.md 不受限。'
        '确需例外请用户在本会话里明确批准后由其调整钩子。\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
