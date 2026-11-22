#!/usr/bin/env python3
"""Claude Code hook: report the session's state to herdr-core via OSC 7501.

Codex and pi speak the Program Status Protocol themselves; Claude Code (2.1.x) does not, so the
herdr window cannot tell whether a Claude pane is working, blocked or done. Registered for the
lifecycle hook events in ~/.claude/settings.json, this script writes one OSC 7501 report to the
controlling terminal. Outside a herdr-core terminal (no HC_TERMINAL_ID) it does nothing, so the same
hooks are harmless in iTerm2, the desktop app and the IDE. It never blocks Claude: every failure exits 0.
"""
import json
import os
import sys

APP = "claude-code"

# hook event -> (state, kind); kind only for blocked
EVENTS = {
    "SessionStart": ("idle", None),
    "UserPromptSubmit": ("working", None),
    "PreToolUse": ("working", None),
    "PostToolUse": ("working", None),
    "PermissionRequest": ("blocked", "permission"),
    "Stop": ("done", None),
    "StopFailure": ("error", None),
}
NOTIFICATION_KINDS = {"permission_prompt": "permission", "elicitation_dialog": "question"}


def report(event: dict) -> str | None:
    name = event.get("hook_event_name", "")
    if name == "Notification":
        kind = NOTIFICATION_KINDS.get(event.get("notification_type", ""))
        return f"state=blocked:kind={kind}:app={APP}" if kind else None
    state, kind = EVENTS.get(name, (None, None))
    if state is None:
        return None
    return f"state={state}" + (f":kind={kind}" if kind else "") + f":app={APP}"


def main() -> None:
    if not os.environ.get("HC_TERMINAL_ID"):
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    payload = report(event)
    if payload is None:
        return
    with open("/dev/tty", "w") as tty:
        tty.write(f"\033]7501;{payload}\033\\")


if __name__ == "__main__":
    try:
        main()
    except OSError:
        pass
