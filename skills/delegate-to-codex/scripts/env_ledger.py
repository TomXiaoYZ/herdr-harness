#!/usr/bin/env python3
"""Environment ledger: every worktree / container stack an agent borrows, with its owner and return condition.

One JSON file, keyed by the environment's identity (realpath for worktrees, `docker:<project-or-label>` for
container stacks). Writers: task_worktree.py (open/return), codex_thread.py (attach thread + card),
workspace_hygiene.py (reconcile: mark returned, record orphans). Readers: workspace_hygiene.py,
review_delegations.py.

Borrowed from Vibe Kanban (ledger is the source of truth, directories without a record are orphans) and
Codex App (archive the parent object → the environment goes).
"""
import json
import os
import time

LEDGER = os.path.expanduser('~/.agents/work-plans/environments.json')
RETURN_CONDITIONS = ('card_accepted', 'pr_merged', 'session_archived', 'manual')


def _load():
    try:
        with open(LEDGER, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + f'.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, LEDGER)


def key_for_worktree(path):
    return os.path.realpath(path)


def open_env(key, *, kind, repo, owner, task, return_condition, **extra):
    """Record a borrowed environment. Re-opening an existing key updates it and clears any return record."""
    if return_condition not in RETURN_CONDITIONS:
        raise ValueError(f'unknown return_condition {return_condition!r}')
    data = _load()
    row = data.get(key, {})
    row.update({'kind': kind, 'repo': os.path.realpath(repo) if repo else None, 'owner': owner, 'task': task,
                'return_condition': return_condition, 'opened_at': row.get('opened_at') or time.strftime('%Y-%m-%dT%H:%M:%S')})
    row.pop('returned_at', None)
    row.pop('returned_by', None)
    row.update(extra)
    data[key] = row
    _save(data)
    return row


def attach(key, **fields):
    """Add fields (thread id, card path, docker project …) to an existing record; creates nothing."""
    data = _load()
    if key in data:
        data[key].update(fields)
        _save(data)
        return data[key]
    return None


def return_env(key, by, note=None):
    data = _load()
    row = data.get(key)
    if row is None:
        return None
    row['returned_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    row['returned_by'] = by
    if note:
        row['return_note'] = note
    _save(data)
    return row


def entries(include_returned=False):
    return {k: v for k, v in _load().items() if include_returned or not v.get('returned_at')}


if __name__ == '__main__':
    import sys
    rows = entries(include_returned='--all' in sys.argv)
    for k, v in sorted(rows.items(), key=lambda kv: kv[1].get('opened_at', '')):
        state = f"returned {v['returned_at']} by {v['returned_by']}" if v.get('returned_at') else 'open'
        print(f"{v.get('kind','?'):9} {state:40} {v.get('owner','?'):16} {v.get('task') or '-':40} {k}")
