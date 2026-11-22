#!/usr/bin/env python3
"""Reconcile borrowed environments (git worktrees, Docker leftovers) against the ledger and reality.

  workspace_hygiene.py [--roots ~/Code ...] [--repo <path> ...] [--no-docker] [--no-size]   # report only
  workspace_hygiene.py --apply                                                                # also do the safe class
  workspace_hygiene.py --snapshot-delete <worktree> [...]                                     # dirty worktree → trash + remove

Report goes to ~/.agents/work-plans/hygiene/<YYYY-MM-DD>-<HHMMSS>.md, latest.md links to the newest (summary to stdout).

Classification of a worktree (first match wins). "Done" is read from the PR state via `gh`, never from
ancestry against main: some repositories target non-main integration branches, so ancestry says "ahead by 900" forever.

  missing       directory gone, git still registers it         → safe: `git worktree prune`
  locked        `git worktree lock` held                       → keep (an agent is running)
  dirty         uncommitted or untracked files                 → report; `--snapshot-delete` puts them in the trash first
  clean/done    PR merged or closed                            → safe: remove worktree + delete local branch
  clean/stale   no PR, last commit > STALE_DAYS ago            → safe: remove worktree, keep the branch (commits stay reachable)
  clean/open    PR open, or recent work                        → keep
Removal of a clean worktree never loses commits: they live on the branch. Dirty worktrees are never removed
without a snapshot (rule: cleanup moves to the trash and leaves a manifest).

Docker: dangling images → safe prune. Exited containers older than STALE_CONTAINER_DAYS with no compose
project (ad-hoc `docker run` test DBs) → safe rm. Compose projects whose containers are all exited → report
with the `down` command. Volumes are never touched here; unused ones are listed with sizes for the owning
project session to decide (volumes may hold irreproducible data). Borrowed from Vibe Kanban (reconcile against a ledger), Codex App (snapshot before delete),
Orca (delete merged branches, keep unmerged).
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'skills', 'delegate-to-codex', 'scripts'))
try:
    import env_ledger
except ImportError:  # running from an installed copy without the skill beside it
    env_ledger = None

ROOTS = ['~/Code']
SKIP_DIRS = {'node_modules', '.worktrees', '.claude', '.codex', '_archive', '.venv', 'venv'}
STALE_DAYS = 14
STALE_CONTAINER_DAYS = 7
REPORT_DIR = os.path.expanduser('~/.agents/work-plans/hygiene')
TRASH = os.path.expanduser('~/.Trash/agent-worktrees')
NOW = time.time()


def run(cmd, cwd=None, timeout=120):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, '', str(e))


def git(repo, *args):
    return run(['git', '-C', repo, *args]).stdout


def find_repos(roots, depth=3):
    found = {}
    for root in roots:
        root = os.path.expanduser(root)
        for cur, dirs, _files in os.walk(root):
            rel_depth = cur[len(root):].count(os.sep)
            if '.git' in dirs:  # a main checkout (worktrees have a .git *file*)
                found[os.path.realpath(cur)] = True
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')] if rel_depth < depth else []
    return sorted(found)


def owner_from_path(path):
    if '/.claude/worktrees/' in path:
        return 'claude-code'
    if '/.worktrees/' in path:
        return 'codex-delegate'
    if '/.codex/worktrees/' in path:
        return 'codex-app'
    return 'manual'


def pr_state(repo, branch, cache):
    if not branch or branch in cache:
        return cache.get(branch)
    r = run(['gh', 'pr', 'list', '--head', branch, '--state', 'all', '--limit', '1',
             '--json', 'state,number,baseRefName,url'], cwd=repo, timeout=40)
    row = None
    if r.returncode == 0:
        try:
            rows = json.loads(r.stdout or '[]')
            row = rows[0] if rows else {'state': 'NONE'}
        except ValueError:
            row = None
    cache[branch] = row
    return row


def du(path):
    r = run(['du', '-sk', path], timeout=300)
    try:
        return int(r.stdout.split()[0]) * 1024
    except (IndexError, ValueError):
        return None


def human(n):
    if n is None:
        return '?'
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if n < 1024:
            return f'{n:.0f}{unit}' if unit == 'B' else f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}P'


def scan_worktrees(repo, ledger, want_size, has_gh):
    rows = []
    blocks = git(repo, 'worktree', 'list', '--porcelain').strip().split('\n\n')
    pr_cache = {}
    for blk in blocks[1:]:
        d = {}
        for line in blk.splitlines():
            k, _, v = line.partition(' ')
            d[k] = v
        path = d.get('worktree')
        if not path:
            continue
        real = os.path.realpath(path)
        branch = d.get('branch', '').replace('refs/heads/', '')
        row = {'repo': repo, 'path': real, 'branch': branch or '(detached)', 'owner': owner_from_path(real),
               'ledger': ledger.get(real), 'locked': 'locked' in d}
        if not os.path.isdir(real):
            row.update(cls='missing', action='prune')
            rows.append(row)
            continue
        dirty = git(real, 'status', '--porcelain').strip()
        ts = git(real, 'log', '-1', '--format=%ct').strip()
        commit_age = int((NOW - int(ts)) / 86400) if ts.isdigit() else None
        # A worktree opened yesterday on an old branch is not stale: age is the younger of last commit and
        # the worktree's own registration (its .git file), so a session that re-created its worktree keeps it.
        try:
            reg_age = int((NOW - os.path.getmtime(os.path.join(real, '.git'))) / 86400)
        except OSError:
            reg_age = None
        row['age_days'] = min(a for a in (commit_age, reg_age) if a is not None) if (commit_age is not None or reg_age is not None) else None
        row['dirty_files'] = len(dirty.splitlines()) if dirty else 0
        row['size'] = du(real) if want_size else None
        pr = pr_state(repo, branch, pr_cache) if (has_gh and branch) else None
        row['pr'] = pr
        if row['locked']:
            row.update(cls='locked', action='keep')
        elif row['owner'] == 'codex-app' and not dirty:
            # Codex App owns these (archive the chat → it deletes the worktree, with a snapshot); report, don't race it.
            row.update(cls='codex-app', action='archive the chat in Codex App')
        elif dirty:
            row.update(cls='dirty', action='snapshot-delete?')
        elif pr and pr.get('state') in ('MERGED', 'CLOSED'):
            row.update(cls='clean/done', action='remove+delete-branch')
        elif pr and pr.get('state') == 'OPEN':
            row.update(cls='clean/open', action='keep')
        elif (row['age_days'] or 0) > STALE_DAYS:
            row.update(cls='clean/stale', action='remove(keep branch)')
        else:
            row.update(cls='clean/open', action='keep')
        rows.append(row)
    return rows


def docker_scan():
    out = {'available': False, 'containers': [], 'compose_idle': {}, 'dangling_images': 0, 'volumes': []}
    if run(['docker', 'info'], timeout=20).returncode != 0:
        return out
    out['available'] = True
    r = run(['docker', 'ps', '-a', '--format', '{{json .}}'], timeout=60)
    for line in r.stdout.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        labels = dict(kv.split('=', 1) for kv in c.get('Labels', '').split(',') if '=' in kv)
        project = labels.get('com.docker.compose.project')
        created = run(['docker', 'inspect', '-f', '{{.State.FinishedAt}}|{{.State.Status}}', c['ID']], timeout=20).stdout.strip()
        finished, _, status = created.partition('|')
        age = None
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(finished.replace('Z', '+00:00'))).days
        except ValueError:
            pass
        row = {'id': c['ID'], 'name': c.get('Names'), 'image': c.get('Image'), 'status': status, 'project': project,
               'task': labels.get('agent.task'), 'exited_days': age}
        if status == 'exited' and project is None and (age or 0) >= STALE_CONTAINER_DAYS:
            row['action'] = 'rm'
        elif status == 'exited' and project:
            out['compose_idle'].setdefault(project, []).append(row)
            row['action'] = f'compose -p {project} down'
        else:
            row['action'] = 'keep'
        out['containers'].append(row)
    # a compose project with any running container is not idle
    for p in list(out['compose_idle']):
        if any(c['project'] == p and c['status'] == 'running' for c in out['containers']):
            del out['compose_idle'][p]
    out['dangling_images'] = len(run(['docker', 'images', '-f', 'dangling=true', '-q'], timeout=30).stdout.split())
    # json-file logs grow without bound unless the daemon has log-opts; surface the big ones.
    r = run(['docker', 'ps', '-a', '-q'], timeout=30)
    big_logs = []
    for cid in r.stdout.split():
        info = run(['docker', 'inspect', '-f', '{{.Name}}|{{.LogPath}}', cid], timeout=20).stdout.strip()
        name, _, logpath = info.partition('|')
        size = run(['docker', 'run', '--rm', '-v', '/var/lib/docker/containers:/c:ro', 'alpine', 'stat', '-c', '%s',
                    logpath.replace('/var/lib/docker/containers', '/c')], timeout=30).stdout.strip() if logpath else ''
        if size.isdigit() and int(size) > 50 * 2**20:
            big_logs.append((name.lstrip('/'), int(size)))
    out['big_logs'] = sorted(big_logs, key=lambda x: -x[1])
    dangling = set(run(['docker', 'volume', 'ls', '-q', '-f', 'dangling=true'], timeout=30).stdout.split())
    r = run(['docker', 'system', 'df', '-v', '--format', '{{json .}}'], timeout=120)
    sizes = {}
    try:
        data = json.loads(r.stdout) if r.stdout.strip().startswith('{') else {}
        for v in data.get('Volumes') or []:
            sizes[v.get('Name')] = v.get('Size')
    except ValueError:
        pass
    out['volumes'] = sorted(({'name': v, 'size': sizes.get(v, '?')} for v in dangling), key=lambda x: x['name'])
    return out


def snapshot_delete(path, by='workspace_hygiene'):
    wt = os.path.realpath(path)
    if not os.path.isdir(wt):
        sys.exit(f'{wt} 不存在')
    common = git(wt, 'rev-parse', '--git-common-dir').strip()
    repo = os.path.dirname(os.path.realpath(common if os.path.isabs(common) else os.path.join(wt, common)))
    branch = git(wt, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(TRASH, f'{os.path.basename(repo)}--{os.path.basename(wt)}--{stamp}')
    os.makedirs(dest, exist_ok=True)
    status = git(wt, 'status', '--porcelain=v1', '--untracked-files=all')
    with open(os.path.join(dest, 'tracked-changes.patch'), 'w', encoding='utf-8') as fh:
        fh.write(run(['git', '-C', wt, 'diff', '--binary', 'HEAD'], timeout=600).stdout)
    untracked = [l[3:] for l in status.splitlines() if l.startswith('??')]
    if untracked:
        with tarfile.open(os.path.join(dest, 'untracked.tar.gz'), 'w:gz') as tar:
            for rel in untracked:
                full = os.path.join(wt, rel)
                if os.path.exists(full):
                    tar.add(full, arcname=rel)
    manifest = {'worktree': wt, 'repo': repo, 'branch': branch, 'head': git(wt, 'rev-parse', 'HEAD').strip(),
                'snapshot_at': stamp, 'status': status, 'restore': [
                    f'git -C {repo} worktree add <path> {branch}',
                    f'git -C <path> apply --binary {dest}/tracked-changes.patch',
                    f'tar -xzf {dest}/untracked.tar.gz -C <path>'] }
    with open(os.path.join(dest, 'MANIFEST.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    r = run(['git', '-C', repo, 'worktree', 'remove', '--force', wt], timeout=600)
    if r.returncode:
        sys.exit(f'快照已存 {dest}，但 worktree remove 失败：{r.stderr.strip()}')
    if env_ledger:
        env_ledger.return_env(env_ledger.key_for_worktree(wt), by=by, note=f'snapshot {dest}; branch {branch} kept')
    print(json.dumps({'snapshot': dest, 'removed': wt, 'branch_kept': branch, 'untracked_files': len(untracked)}, ensure_ascii=False))
    return dest


def apply_safe(rows, docker, log):
    for r in rows:
        if r['cls'] == 'missing':
            run(['git', '-C', r['repo'], 'worktree', 'prune'])
            log.append(f"prune: {r['path']}")
        elif r['cls'] == 'clean/done':
            res = run(['git', '-C', r['repo'], 'worktree', 'remove', r['path']], timeout=600)
            if res.returncode:
                log.append(f"FAILED remove {r['path']}: {res.stderr.strip()}")
                continue
            note = 'removed'
            if r['branch'] != '(detached)':
                b = run(['git', '-C', r['repo'], 'branch', '-D', r['branch']])
                note += '; branch deleted' if b.returncode == 0 else f'; branch kept ({b.stderr.strip()})'
            log.append(f"{note}: {r['path']} (PR {r['pr'].get('state')} #{r['pr'].get('number')})")
            if env_ledger:
                env_ledger.return_env(r['path'], by='workspace_hygiene', note=note)
        elif r['cls'] == 'clean/stale':
            res = run(['git', '-C', r['repo'], 'worktree', 'remove', r['path']], timeout=600)
            log.append((f"removed (branch {r['branch']} kept): " if res.returncode == 0 else 'FAILED remove: ') + r['path'])
            if res.returncode == 0 and env_ledger:
                env_ledger.return_env(r['path'], by='workspace_hygiene', note='stale, branch kept')
    if docker['available']:
        for c in docker['containers']:
            if c['action'] == 'rm':
                res = run(['docker', 'rm', c['id']], timeout=60)
                log.append(f"docker rm {c['name']}" + ('' if res.returncode == 0 else f' FAILED {res.stderr.strip()}'))
        if docker['dangling_images']:
            run(['docker', 'image', 'prune', '-f'], timeout=120)
            log.append(f"docker image prune: {docker['dangling_images']} dangling")
        # Build cache older than a week is pure cache (rebuildable); keep the recent week warm.
        r = run(['docker', 'builder', 'prune', '-f', '--filter', 'until=168h'], timeout=600)
        if r.returncode == 0:
            last = (r.stdout.strip().splitlines() or ['0B'])[-1]
            log.append(f'docker builder prune (until=168h): {last}')


def write_report(rows, docker, ledger, log, applied, reconciled=()):
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = dt.date.today().isoformat()
    # One file per run (a scoped --repo test must not overwrite the weekly run's report); latest.md points at the newest.
    path = os.path.join(REPORT_DIR, f'{date}-{dt.datetime.now().strftime("%H%M%S")}.md')
    by_cls = {}
    for r in rows:
        by_cls.setdefault(r['cls'], []).append(r)
    out = [f'# 工作区对账 {date}', '',
           f"worktree {len(rows)} 个：" + '、'.join(f'{k} {len(v)}' for k, v in sorted(by_cls.items())), '']
    order = ['clean/done', 'missing', 'clean/stale', 'dirty', 'codex-app', 'locked', 'clean/open']
    titles = {'clean/done': '可直接收回（PR 已 merged/closed，目录干净）', 'missing': '幽灵注册（目录已不存在）',
              'clean/stale': f'干净但无 PR 且 {STALE_DAYS} 天没动（删目录留分支）', 'dirty': '有未提交改动（先快照进废纸篓再删，需用户看一眼）',
              'codex-app': 'Codex App 管理的工作树（干净；在 App 里归档对应聊天即删，不在这里动）',
              'locked': '被锁（agent 运行中）', 'clean/open': '进行中（保留）'}
    for cls in order:
        items = by_cls.get(cls) or []
        if not items:
            continue
        out += [f'## {titles[cls]}（{len(items)}）', '', '| 仓库 | 路径 | 分支 | PR | 最后提交 | 改动 | 体积 | 来源 | 账本 |', '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for r in sorted(items, key=lambda x: (x['repo'], x['path'])):
            pr = r.get('pr') or {}
            prs = f"{pr.get('state','-')} #{pr.get('number')}→{pr.get('baseRefName')}" if pr.get('number') else (pr.get('state', '-') if pr else '-')
            led = '有' if r.get('ledger') else '无'
            out.append(f"| {os.path.basename(r['repo'])} | `{r['path'].replace(os.path.expanduser('~'), '~')}` | {r['branch']} | {prs} | "
                       f"{r.get('age_days', '?')}d | {r.get('dirty_files', 0)} | {human(r.get('size'))} | {r['owner']} | {led} |")
        out.append('')
    if docker['available']:
        rm = [c for c in docker['containers'] if c['action'] == 'rm']
        out += ['## Docker', '', f"- 已退出且超过 {STALE_CONTAINER_DAYS} 天的零散容器（安全类，rm）：{len(rm)}" + (' → ' + '、'.join(c['name'] for c in rm) if rm else ''),
                f"- 悬空镜像：{docker['dangling_images']}",
                f"- 整组已停的 compose 项目（报告，不自动动）：" + ('、'.join(f"`{p}`（{len(cs)} 个容器）" for p, cs in docker['compose_idle'].items()) or '无'),
                f"- 无人使用的卷（永不自动删，交项目会话判断）：{len(docker['volumes'])}",
                f"- 日志超过 50 MB 的容器（建议给 OrbStack 的 docker.json 加 log-opts max-size）：" + ('、'.join(f"{n} {human(s)}" for n, s in docker.get('big_logs') or []) or '无')]
        for v in docker['volumes']:
            out.append(f"  - {v['size']}\t`{v['name']}`")
        out.append('')
    open_ledger = {k: v for k, v in ledger.items() if not v.get('returned_at')}
    unrecorded = [r['path'] for r in rows if r['owner'] == 'codex-delegate' and not r.get('ledger') and r['cls'] != 'missing']
    out += ['## 账本对账', '', f'- 账本里未归还的环境：{len(open_ledger)}；本次按“目录已不存在”补记归还：{len(reconciled)}',
            f'- `.worktrees/` 下没有账本记录的任务工作树（2026-11-10 之前建的）：{len(unrecorded)}', '']
    if log:
        out += [f"## {'已执行' if applied else '将执行'}的安全类动作", ''] + [f'- {l}' for l in log] + ['']
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(out))
    latest = os.path.join(REPORT_DIR, 'latest.md')
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(path), latest)
    except OSError:
        pass
    return path, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--roots', nargs='*', default=ROOTS)
    ap.add_argument('--repo', nargs='*', help='only these repositories')
    ap.add_argument('--no-docker', action='store_true')
    ap.add_argument('--no-size', action='store_true', help='skip du (fast)')
    ap.add_argument('--apply', action='store_true', help='execute the safe class')
    ap.add_argument('--snapshot-delete', nargs='*', metavar='WORKTREE', help='dirty worktrees: trash snapshot, then remove')
    a = ap.parse_args()
    if a.snapshot_delete:
        for p in a.snapshot_delete:
            snapshot_delete(p)
        return
    ledger = env_ledger.entries(include_returned=False) if env_ledger else {}
    # Reconcile the ledger with reality: a recorded worktree whose directory is gone was returned without
    # task_worktree.py (plain `git worktree remove`, or the app); mark it so the ledger stays the source of truth.
    reconciled = []
    for key, row in list(ledger.items()):
        if row.get('kind') == 'worktree' and not os.path.isdir(key):
            env_ledger.return_env(key, by='workspace_hygiene/reconcile', note='directory gone')
            reconciled.append(key)
            del ledger[key]
    repos = [os.path.realpath(os.path.expanduser(r)) for r in a.repo] if a.repo else find_repos(a.roots)
    has_gh = run(['gh', 'auth', 'status'], timeout=20).returncode == 0
    rows = []
    for repo in repos:
        rows += scan_worktrees(repo, ledger, not a.no_size, has_gh)
    docker = {'available': False} if a.no_docker else docker_scan()
    log = []
    if a.apply:
        apply_safe(rows, docker, log)
    else:
        for r in rows:
            if r['cls'] in ('missing', 'clean/done', 'clean/stale'):
                log.append(f"{r['action']}: {r['path']}")
        if docker.get('available'):
            log += [f"docker rm {c['name']}" for c in docker['containers'] if c['action'] == 'rm']
    path, out = write_report(rows, docker, ledger, log, a.apply, reconciled)
    print('\n'.join(out[:3]))
    print(f'报告：{path}')
    if not a.apply and log:
        print(f'安全类待执行 {len(log)} 项（--apply 执行）')


if __name__ == '__main__':
    main()
