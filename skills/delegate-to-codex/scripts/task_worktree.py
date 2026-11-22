#!/usr/bin/env python3
"""Open or return a delegated task's worktree, the only sanctioned way to create one.

  task_worktree.py new    --repo <root> --task <task> [--base <ref>] [--allow-over-cap]
  task_worktree.py return --worktree <path> [--keep-branch] [--by <who>] [--note <text>]

new:    `<repo>/.worktrees/<task>` on branch `task/<task>` from <base> (default: the repository's integration
        branch via origin/HEAD, else HEAD). Refuses when the repository already has MAX_TASK_WORKTREES task
        worktrees (the cap is the backstop every mature harness has; clean up first). When `<repo>/.worktree-sparse`
        exists, the worktree is a sparse checkout that leaves out the directories listed there (one path per
        line, `#` comments): for one data-heavy repository that turns a 5 GB copy of tracked data into a 350 MB checkout.
        Records the environment in the ledger (owner codex-delegate, return condition card_accepted).
return: removes the worktree directory. The branch is deleted only when it carries no commits beyond its
        upstream (or has no upstream and --keep-branch is absent and the branch is merged into origin/HEAD);
        otherwise the branch stays and the ledger note says so. A dirty worktree is refused: snapshot it with
        `workspace_hygiene.py --snapshot-delete <path>` instead, which puts the changes in the trash with a manifest.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import env_ledger  # noqa: E402

MAX_TASK_WORKTREES = 8
WORKTREES_DIR = '.worktrees'
SPARSE_FILE = '.worktree-sparse'


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=check)


def task_worktrees(repo):
    out = git(repo, 'worktree', 'list', '--porcelain').stdout
    paths = [l.split(' ', 1)[1] for l in out.splitlines() if l.startswith('worktree ')]
    marker = f'/{WORKTREES_DIR}/'
    return [p for p in paths[1:] if marker in p]


def default_base(repo):
    head = git(repo, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD', check=False).stdout.strip()
    return head.replace('refs/remotes/', '') if head else 'HEAD'


def cmd_new(a):
    repo = os.path.realpath(a.repo)
    if not os.path.isdir(os.path.join(repo, '.git')):
        sys.exit(f'{repo} 不是主检出（.git 不是目录）；任务工作树只从主检出建')
    existing = task_worktrees(repo)
    if len(existing) >= MAX_TASK_WORKTREES and not a.allow_over_cap:
        sys.exit(f'仓库已有 {len(existing)} 个任务工作树（上限 {MAX_TASK_WORKTREES}），先收口再派：\n  ' + '\n  '.join(existing)
                 + '\n查看哪些可收：workspace_hygiene.py --repo ' + repo)
    wt = os.path.join(repo, WORKTREES_DIR, a.task)
    branch = f'task/{a.task}'
    if os.path.exists(wt):
        sys.exit(f'{wt} 已存在')
    base = a.base or default_base(repo)
    exclude = os.path.join(repo, '.git', 'info', 'exclude')
    try:
        current = open(exclude, encoding='utf-8').read() if os.path.exists(exclude) else ''
        if f'{WORKTREES_DIR}/' not in current:
            with open(exclude, 'a', encoding='utf-8') as fh:
                fh.write(f'\n{WORKTREES_DIR}/\n')
    except OSError:
        pass
    sparse_path = os.path.join(repo, SPARSE_FILE)
    sparse = [l.strip().strip('/') for l in open(sparse_path, encoding='utf-8')
              if l.strip() and not l.lstrip().startswith('#')] if os.path.exists(sparse_path) else []
    add = ['worktree', 'add'] + (['--no-checkout'] if sparse else []) + [wt, '-b', branch, base]
    r = git(repo, *add, check=False)
    if r.returncode:
        sys.exit(f'git worktree add 失败：{r.stderr.strip()}')
    if sparse:
        git(wt, 'sparse-checkout', 'set', '--no-cone', '/*', *[f'!/{p}/' for p in sparse])
        git(wt, 'checkout')
    env_ledger.open_env(env_ledger.key_for_worktree(wt), kind='worktree', repo=repo, owner='codex-delegate',
                        task=a.task, return_condition='card_accepted', branch=branch, base=base, sparse=sparse)
    print(json.dumps({'worktree': wt, 'branch': branch, 'base_commit': git(wt, 'rev-parse', 'HEAD').stdout.strip(),
                      'sparse': sparse, 'task_worktrees': len(existing) + 1, 'cap': MAX_TASK_WORKTREES}, ensure_ascii=False))


def cmd_return(a):
    wt = os.path.realpath(a.worktree)
    if not os.path.isdir(wt):
        sys.exit(f'{wt} 不存在；若 git 还记着它，用 workspace_hygiene.py --apply 做 prune')
    repo = git(wt, 'rev-parse', '--git-common-dir').stdout.strip()
    repo = os.path.realpath(os.path.join(wt, repo) if not os.path.isabs(repo) else repo)
    repo = os.path.dirname(repo)
    if git(wt, 'status', '--porcelain').stdout.strip():
        sys.exit(f'{wt} 有未提交改动或未跟踪文件；不直接删。用 workspace_hygiene.py --snapshot-delete {wt} 先快照进废纸篓')
    branch = git(wt, 'rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    git(repo, 'worktree', 'remove', wt)
    branch_note = 'branch kept'
    if branch != 'HEAD' and not a.keep_branch:
        upstream = git(repo, 'rev-parse', '--abbrev-ref', f'{branch}@{{upstream}}', check=False)
        if upstream.returncode == 0:
            ahead = git(repo, 'rev-list', '--count', f'{upstream.stdout.strip()}..{branch}').stdout.strip()
            if ahead == '0':
                git(repo, 'branch', '-D', branch)
                branch_note = f'branch deleted (no commits beyond {upstream.stdout.strip()})'
            else:
                branch_note = f'branch kept ({ahead} commits not on {upstream.stdout.strip()})'
        else:
            base = default_base(repo)
            ahead = git(repo, 'rev-list', '--count', f'{base}..{branch}', check=False).stdout.strip()
            if ahead == '0':
                git(repo, 'branch', '-D', branch)
                branch_note = f'branch deleted (merged into {base})'
            else:
                branch_note = f'branch kept ({ahead} commits not on {base}, no upstream)'
    env_ledger.return_env(env_ledger.key_for_worktree(wt), by=a.by, note=f'{a.note or ""} {branch_note}'.strip())
    print(json.dumps({'removed': wt, 'branch': branch, 'note': branch_note}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    n = sub.add_parser('new')
    n.add_argument('--repo', required=True)
    n.add_argument('--task', required=True)
    n.add_argument('--base')
    n.add_argument('--allow-over-cap', action='store_true', help='only with a reason written in the card')
    n.set_defaults(fn=cmd_new)
    r = sub.add_parser('return')
    r.add_argument('--worktree', required=True)
    r.add_argument('--keep-branch', action='store_true')
    r.add_argument('--by', default='project-session')
    r.add_argument('--note')
    r.set_defaults(fn=cmd_return)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
