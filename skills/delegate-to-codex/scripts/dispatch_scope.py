#!/usr/bin/env python3
"""One card, one fresh Claude Code context.

On 11-14 two subagents dispatched and accepted 26 and 8 cards each inside one context: ~420M Claude Code
tokens, almost all re-reading a ~370K-token context, against 49M Codex tokens that day. The rule "一张卡一条任务会话"
was in the skill but nothing held it, and a wrapper script (dispatch.sh) hid every codex_thread.py call from hooks.

So the check lives in `codex_thread.py start` itself. The caller is the Claude Code context that ran the command:
the session from CLAUDE_CODE_SESSION_ID, and inside it the transcript (main or subagents/agent-*.jsonl) whose newest
Bash tool call has no result yet. A caller that already started a thread for a different card is refused; later
rounds of the same card pass. Outside Claude Code (no session id) or when the caller cannot be told apart, it passes:
the check never blocks the user's own use by accident.

    python3 dispatch_scope.py caller        # prints the detected caller, for checking the detection
"""
import glob
import json
import os
import sys
import time

PROJECTS = os.path.expanduser('~/.claude/projects')
TAIL_BYTES = 512 * 1024
WAIT_TRIES = 10


def _pending_bash(path):
    """Timestamp of the newest Bash tool_use in this transcript that has no tool_result yet, else None."""
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            lines = fh.read().decode('utf-8', 'ignore').splitlines()
    except OSError:
        return None
    uses, done = {}, set()
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'tool_use' and part.get('name') == 'Bash':
                uses[part.get('id')] = rec.get('timestamp') or ''
            elif part.get('type') == 'tool_result':
                done.add(part.get('tool_use_id'))
    pending = [ts for tid, ts in uses.items() if tid not in done]
    return max(pending) if pending else None


def caller():
    """'<session>:main' or '<session>:<agent-id>' for the context running this process, or None if unknown."""
    session = os.environ.get('CLAUDE_CODE_SESSION_ID')
    if not session:
        return None
    found = []
    for _ in range(WAIT_TRIES):
        # The transcript is flushed asynchronously; the running Bash call shows up within a second or two.
        files = glob.glob(os.path.join(PROJECTS, '*', f'{session}.jsonl'))
        files += glob.glob(os.path.join(PROJECTS, '*', session, 'subagents', '*.jsonl'))
        found = sorted(((ts, f) for f in files for ts in [_pending_bash(f)] if ts), reverse=True)
        if found:
            break
        time.sleep(0.5)
    if not found:
        return None
    if len(found) > 1 and found[0][0] == found[1][0]:
        return None  # two contexts waiting on Bash at the same instant: cannot tell which one this is
    path = found[0][1]
    who = 'main' if os.path.basename(path) == f'{session}.jsonl' else os.path.basename(path)[:-len('.jsonl')]
    return f'{session}:{who}'


def check(threads_dir, card):
    """Refusal message if this caller already started a thread for another card, else None. Returns (message, caller)."""
    who = caller()
    if not who or not card:
        return None, who
    card = os.path.realpath(card)
    for path in glob.glob(os.path.join(threads_dir, '*.json')):
        try:
            with open(path, encoding='utf-8') as fh:
                reg = json.load(fh)
        except (OSError, ValueError):
            continue
        touched = reg.get('caller') == who or who in (reg.get('turn_callers') or [])
        if touched and reg.get('card') and os.path.realpath(reg['card']) != card:
            return (f'一卡一个新上下文：这个 Claude Code 上下文（{who}）已经派过或驱动过 {reg["card"]}，不能再碰 {card}。'
                    '回到项目会话，为这张卡新开一条 `start_session`（fresh）或一个新的 Agent 子代理，只带这张卡的简报'
                    '（assets/acceptance-agent-brief.md）；同一张卡的第 2 轮不受限。', who)
    return None, who


def check_turn(threads_dir, thread):
    """Same rule for `turn` (2026-11-15): on 11-15 a G4 subagent let throwaway subagents run only `start` and drove
    `turn` and acceptance for 14 cards itself (~132M tokens re-reading one context). A registered thread may be driven
    only by a context that has touched no other card; the caller is recorded so it cannot move on to the next card.
    Unregistered threads (the user's own) and undetectable callers pass. Returns a refusal message or None."""
    path = os.path.join(threads_dir, f'{thread}.json')
    try:
        with open(path, encoding='utf-8') as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return None
    if not reg.get('card'):
        return None
    refusal, who = check(threads_dir, reg['card'])
    if refusal or not who:
        return refusal
    if who != reg.get('caller') and who not in (reg.get('turn_callers') or []):
        reg['turn_callers'] = (reg.get('turn_callers') or []) + [who]
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(reg, fh, ensure_ascii=False)
    return None


if __name__ == '__main__':
    if sys.argv[1:] == ['caller']:
        print(caller())
    else:
        sys.exit(__doc__)
