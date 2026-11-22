#!/usr/bin/env python3
"""herdr channel for delegate-to-codex: run a card's Codex implementer as a herdr-core terminal instead of a
Codex desktop-app thread, so CC, Codex, opencode and pi work side by side in the repository's herdr space.

  herdr_dispatch.py start  --repo <root> --worktree <wt> --card <card> --base <commit> --name <task>
  herdr_dispatch.py turn   --name <task> (prompt on stdin or --prompt)       # round 2 in the same Codex session
  herdr_dispatch.py wait   --name <task> --round N [--timeout 170]          # waits a slice; exit 4 = still working, run it again
  herdr_dispatch.py review --name <task> [--model deepseek/...]             # read-only opencode/DeepSeek review
  herdr_dispatch.py stop   --name <task>                                    # end and remove its terminals

Same gates as codex_thread.py: the card's "## 授权" section is the only source of network, writable roots, MCP,
model and effort (card_mandate); one Claude Code context drives one card (dispatch_scope); the Codex session is
registered under _threads/<session id>.json, so ~/.codex/hooks.json's Stop hook (codex_stop_audit.py) audits it
at every turn end; the worktree is attached to the environment ledger. Evidence goes to
~/.agents/work-plans/evidence/delegate/<repo>/<task>/ (start.json, receipt-N.md, run-N.jsonl, turn-N.out, review-N.md) in the codex_thread.py layout, so
review_delegations.py counts herdr cards too; never the worktree.
Terminals carry herdr.space/herdr.tab labels when the repository has a herdr space, so they show in the window.
No credentials are read here; the review wrapper reads its key from the Keychain itself. stdlib only.
"""
import argparse
import glob
import json
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from codex_thread import EVIDENCE_ROOT, card_mandate, configured_mcp_servers  # noqa: E402
import dispatch_scope  # noqa: E402

HC = os.path.expanduser('~/.local/bin/hc')
HERDR_APP = os.path.expanduser('~/.local/bin/herdr-app')
CODEX = '/opt/homebrew/bin/codex'
REVIEW = os.path.expanduser('~/.local/bin/hc-opencode-deepseek')
THREADS = os.path.join(EVIDENCE_ROOT, '_threads')
SESSIONS = os.path.expanduser('~/.codex/sessions')
DEFAULT_MODEL, DEFAULT_EFFORT = 'gpt-6-astra', 'xhigh'
# MCP servers no mandate can open (e.g. live trading, outbound messaging): CODEX_NEVER_MCP=a,b
NEVER_MCP = {s.strip() for s in os.environ.get('CODEX_NEVER_MCP', '').split(',') if s.strip()}


def hc(*args, check=True):
    res = subprocess.run([HC, *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f'hc {args[0]} failed: {res.stderr.strip() or res.stdout.strip()}')
    return res.stdout


def codex_argv(mandate, prompt):
    """Codex CLI flags equivalent to codex_thread.py's thread/start params for this mandate."""
    argv = [CODEX, '--no-daemon', '-m', mandate['model'] or DEFAULT_MODEL,
            '-c', f'model_reasoning_effort="{mandate["effort"] or DEFAULT_EFFORT}"',
            '-c', 'check_for_update_on_startup=false', '-s', 'workspace-write', '-a', 'never',
            '-c', 'features.hooks=true']   # registered below, so the Stop audit applies
    servers = configured_mcp_servers()
    if mandate['mcp']:
        off = set(mandate['disable_mcp']) | (NEVER_MCP & set(servers))
        unknown = sorted(off - set(servers) - NEVER_MCP)
        if unknown:
            sys.exit(f'unknown MCP server(s) {unknown}; configured: {servers}')
    else:
        off = set(servers)
        argv += ['--disable', 'plugins', '--disable', 'apps']
    argv += [x for name in sorted(off) for x in ('-c', f'mcp_servers.{name}.enabled=false')]
    if mandate['network']:
        argv += ['-c', 'sandbox_workspace_write.network_access=true']
    if mandate['writable']:
        roots = [os.path.realpath(os.path.expanduser(d)) for d in mandate['writable']]
        argv += ['-c', 'sandbox_workspace_write.writable_roots=' + json.dumps(roots)]
    return argv + [prompt]


def space_labels(repo):
    """herdr.space / herdr.tab labels of the window space whose folder is the repository root, if any."""
    try:
        spaces = json.loads(subprocess.run([HERDR_APP, 'ls'], capture_output=True, text=True, timeout=20).stdout)['spaces']
    except Exception:
        return []
    for sp in spaces:
        if os.path.realpath(sp.get('folder') or '') == repo and sp.get('tabs'):
            return ['--label', f'herdr.space={sp["id"]}', '--label', f'herdr.tab={sp["tabs"][0]["id"]}']
    return []


def find_rollout(worktree, since, timeout=90):
    """The Codex CLI session whose session_meta cwd is the worktree and which started after `since`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in sorted(glob.glob(os.path.join(SESSIONS, '*/*/*/rollout-*.jsonl')), key=os.path.getmtime, reverse=True):
            if os.path.getmtime(path) < since:
                break
            try:
                with open(path, encoding='utf-8') as fh:
                    meta = json.loads(fh.readline()).get('payload') or {}
            except (OSError, ValueError):
                continue
            if os.path.realpath(meta.get('cwd') or '') == worktree:
                return meta.get('id') or meta.get('session_id'), path
        time.sleep(2)
    return None, None


def reg_for(name):
    for path in glob.glob(os.path.join(THREADS, '*.json')):
        try:
            reg = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if reg.get('channel') == 'herdr' and reg.get('terminal_name') == name:
            return path, reg
    sys.exit(f'no herdr registration named {name!r} under {THREADS}')


def evidence_dir(reg):
    d = os.path.join(EVIDENCE_ROOT, os.path.basename(reg['cwd'].rstrip('/')), os.path.basename(reg['worktree'].rstrip('/')))
    os.makedirs(d, exist_ok=True)
    return d


def first_prompt(card, worktree, base, evidence):
    return (f'任务卡：{card}\n工作树：{worktree}（分支已检出，基线提交 {base}）\n'
            f'按卡执行第 1 轮。卡的“授权”节是唯一权限来源；只改卡里列出的文件，先写“必须通过的新测试”并确认失败再实现。'
            f'不推送、不合并。结束时按卡的“回执”格式输出回执全文作为最后一条消息（派发方会存到 {evidence}）。')


def cmd_start(a):
    repo, wt, card = (os.path.realpath(p) for p in (a.repo, a.worktree, a.card))
    mandate = card_mandate(card)
    if mandate['mcp']:
        mandate['disable_mcp'] = sorted(set(mandate['disable_mcp']) | (NEVER_MCP & set(configured_mcp_servers())))
    refusal, caller = dispatch_scope.check(THREADS, card)
    if refusal:
        sys.exit(refusal)
    os.makedirs(THREADS, exist_ok=True)
    ev = os.path.join(EVIDENCE_ROOT, os.path.basename(repo), os.path.basename(wt))
    os.makedirs(ev, exist_ok=True)
    since = time.time() - 1
    argv = codex_argv(mandate, first_prompt(card, wt, a.base, ev))
    out = hc('run', '--json', '--bg', '--name', a.name, '--cwd', wt, '--label', f'agent.task={a.name}',
             *space_labels(repo), *argv)
    started = json.loads(out.strip().splitlines()[-1])
    sid, rollout = find_rollout(wt, since)
    if not sid:
        hc('kill', a.name, check=False)
        sys.exit('Codex session did not appear within 90 s; terminal killed. Check `hc read` before retrying.')
    reg = {'worktree': wt, 'card': card, 'base': a.base, 'cwd': repo, 'name': a.name, 'blocks': 0, 'caller': caller,
           'channel': 'herdr', 'terminal': started.get('address'), 'terminal_name': a.name, 'rollout': rollout}
    with open(os.path.join(THREADS, f'{sid}.json'), 'w', encoding='utf-8') as fh:
        json.dump(reg, fh, ensure_ascii=False)
    try:
        import env_ledger
        key = env_ledger.key_for_worktree(wt)
        if env_ledger.attach(key, thread=sid, card=card, terminal=started.get('address')) is None:
            env_ledger.open_env(key, kind='worktree', repo=repo, owner='codex-delegate', task=os.path.basename(key),
                                return_condition='card_accepted', thread=sid, card=card)
    except Exception as error:  # the ledger never blocks a dispatch
        print(f'warning: environment ledger not updated: {error}', file=sys.stderr)
    summary = {'channel': 'herdr', 'sessionId': sid, 'threadId': sid, 'terminal': started.get('address'), 'name': a.name,
               'stopAudit': True, 'card': card, 'worktree': wt, 'base': a.base, 'cwd': repo,
               'mandate': {k: mandate[k] for k in ('network', 'writable', 'mcp', 'disable_mcp')},
               'model': mandate['model'] or DEFAULT_MODEL, 'effort': mandate['effort'] or DEFAULT_EFFORT,
               'rolloutPath': rollout, 'argv': shlex.join(argv[:-1])}
    json.dump(summary, open(os.path.join(ev, 'start.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


def cmd_turn(a):
    path, reg = reg_for(a.name)
    sid = os.path.basename(path)[:-5]
    refusal = dispatch_scope.check_turn(THREADS, sid)
    if refusal:
        sys.exit(refusal)
    prompt = a.prompt if a.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        sys.exit('empty prompt')
    hc('send', a.name, prompt)
    print(json.dumps({'sessionId': sid, 'terminal': reg['terminal'], 'sent': True}, ensure_ascii=False))


def last_turn(rollout):
    """(completed, last assistant text) of the latest turn in a Codex rollout."""
    last, done = None, False
    with open(rollout, encoding='utf-8') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get('payload') or {}
            if d.get('type') == 'response_item' and p.get('type') == 'message':
                if p.get('role') == 'user':
                    last, done = None, False
                elif p.get('role') == 'assistant':
                    last = ''.join(c.get('text', '') for c in p.get('content') or [])
            elif d.get('type') == 'event_msg' and p.get('type') == 'task_complete':
                done = True
    return done, last


WAIT_SLICE = 170  # Claude Code moves a foreground Bash call to the background after 180 s, whatever its timeout


def hc_wait(name, timeout):
    """hc wait, but a message typed into the caller's terminal (another child's note, the user) ends hc's wait early
    with reason=message; that is not the child stopping, so keep waiting until the slice is used up."""
    deadline = time.time() + timeout
    while True:
        res = subprocess.run([HC, 'wait', name, '--timeout', str(max(1, int(deadline - time.time())))],
                             capture_output=True, text=True)
        try:
            waited = json.loads(res.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {'raw': res.stdout.strip() or res.stderr.strip()}
        got = waited.get('wait', waited) if isinstance(waited, dict) else {}
        if got.get('reason') == 'message' and got.get('status') == 'working' and time.time() < deadline - 1:
            continue
        return got


def cmd_wait(a):
    """Wait one slice for the round to end. A Claude Code subagent must keep this in the foreground: a subagent
    that backgrounds the wait and ends its turn hands back to the parent before the verdict, and the parent then
    idles on a notification that may never come (2026-11-21, a batch of UX cards). Exit 4 means Codex is still
    working: run the same command again. Receipt and round files are written only once the round has ended."""
    path, reg = reg_for(a.name)
    sid = os.path.basename(path)[:-5]
    waited = hc_wait(a.name, a.timeout)
    if waited.get('status') == 'working':
        print(json.dumps({'status': 'working', 'terminal': reg['terminal'], 'hc': waited,
                          'next': f'Codex 还在跑：前台再运行同一条 wait（不要放后台、不要结束本轮）'}, ensure_ascii=False))
        sys.exit(4)
    done, last = last_turn(reg['rollout'])
    ev = evidence_dir(reg)
    receipt = os.path.join(ev, f'receipt-{a.round}.md')
    if last is not None:
        with open(receipt, 'w', encoding='utf-8') as fh:
            fh.write(last)
    status = 'completed' if done and waited.get('status') in ('done', 'idle') else (waited.get('status') or 'unknown')
    # run-N.jsonl marks the round for review_delegations.py (it counts rounds by these files); the events themselves
    # stay in the Codex rollout it points to. turn-N.out is the round summary in codex_thread.py's shape.
    with open(os.path.join(ev, f'run-{a.round}.jsonl'), 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'channel': 'herdr', 'threadId': sid, 'rollout': reg['rollout']}) + '\n')
    with open(os.path.join(ev, f'turn-{a.round}.out'), 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'status': status, 'threadId': sid}) + '\n')
    print(json.dumps({'status': status, 'terminal': reg['terminal'], 'hc': waited,
                      'lastMessage': receipt if last is not None else None}, ensure_ascii=False))
    sys.exit(0 if status == 'completed' else 3)


def cmd_review(a):
    _, reg = reg_for(a.name)
    ev = evidence_dir(reg)
    rounds = len(glob.glob(os.path.join(ev, 'review-*.md'))) + 1
    out_file = os.path.join(ev, f'review-{rounds}.md')
    task = (f'复核任务卡 {reg["card"]} 的实现。工作树 {reg["worktree"]}，基线 {reg["base"]}：'
            f'看 git diff {reg["base"]}...HEAD 与未提交改动，对照卡的验收标准和授权节。只读，不改文件。'
            f'把结论全文作为最后一条消息输出；派发方会存到 {out_file}。')
    env = dict(os.environ, **({'HC_DEEPSEEK_MODEL': a.model} if a.model else {}))
    name = f'{a.name}-review'[:32]
    res = subprocess.run([HC, 'run', '--bg', '--json', '--name', name, '--cwd', reg['worktree'],
                          '--label', f'agent.task={a.name}', *space_labels(reg['cwd']), 'opencode-review', task],
                         capture_output=True, text=True, env=env)
    if res.returncode != 0:
        sys.exit(f'hc run failed: {res.stderr.strip() or res.stdout.strip()}')
    print(res.stdout.strip().splitlines()[-1])
    print(json.dumps({'reviewTerminal': name, 'saveTo': out_file,
                      'next': f'hc wait {name}; then save its final message to {out_file}'}, ensure_ascii=False))


def cmd_stop(a):
    _, reg = reg_for(a.name)
    for name in (a.name, f'{a.name}-review'[:32]):
        subprocess.run([HC, 'kill', name], capture_output=True)
        subprocess.run([HC, 'rm', name], capture_output=True)
    print(json.dumps({'stopped': a.name, 'worktree': reg['worktree'],
                      'next': 'return the worktree with task_worktree.py return after accept/merge'}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start')
    for flag in ('--repo', '--worktree', '--card', '--base', '--name'):
        s.add_argument(flag, required=True)
    t = sub.add_parser('turn')
    t.add_argument('--name', required=True)
    t.add_argument('--prompt')
    w = sub.add_parser('wait')
    w.add_argument('--name', required=True)
    w.add_argument('--round', type=int, required=True)
    w.add_argument('--timeout', type=int, default=WAIT_SLICE)
    r = sub.add_parser('review')
    r.add_argument('--name', required=True)
    r.add_argument('--model')
    k = sub.add_parser('stop')
    k.add_argument('--name', required=True)
    a = p.parse_args()
    {'start': cmd_start, 'turn': cmd_turn, 'wait': cmd_wait, 'review': cmd_review, 'stop': cmd_stop}[a.cmd](a)


if __name__ == '__main__':
    main()
