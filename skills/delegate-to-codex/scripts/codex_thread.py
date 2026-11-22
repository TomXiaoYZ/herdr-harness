#!/usr/bin/env python3
"""Drive the Codex desktop app's app-server daemon to run a delegated task as a visible project thread.

The daemon speaks JSON-RPC over WebSocket on a unix socket. Threads created here appear in the Codex
app under the project whose root path equals the thread cwd (verified 2026-10-24, app 26.1021,
codex-cli 0.164): pass the repository root as cwd, never a worktree path, and never override cwd per turn.

  codex_thread.py projects
  codex_thread.py start --cwd <repo-root> --name "<date>｜<project>｜<task>" [--sandbox workspace-write]
                        [--approval never] [--model M] [--effort E] [--network]
  codex_thread.py turn --thread <id> --events <file.jsonl> [--timeout 7200] (prompt on stdin or --prompt)
  codex_thread.py interrupt --thread <id>
  codex_thread.py archive --thread <id>
  codex_thread.py refresh --cwd <repo-root>        # make the app re-read its thread list

Exit code 0 on success; 2 when the daemon is unreachable and `codex app-server daemon start` cannot bring
it back (the daemon is auto-started when the control socket is missing, needed since ChatGPT.app 26.1103);
3 on a declined server request or timeout. No credentials are read or passed. stdlib only.
"""
import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

SOCKET = os.path.expanduser('~/.codex/app-server-control/app-server-control.sock')
APP_CODEX = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
CLIENT = {'name': 'cc-delegate-to-codex', 'title': 'Claude Code delegate', 'version': '0.1'}
EVIDENCE_ROOT = os.path.expanduser('~/.agents/work-plans/evidence/delegate')
NEVER_MCP = {s.strip() for s in os.environ.get('CODEX_NEVER_MCP', '').split(',') if s.strip()}  # no mandate opens these


class WebSocket:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(os.path.realpath(path))
        self.buf = b''
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        while b'\r\n\r\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('handshake: socket closed')
            self.buf += chunk
        head, _, self.buf = self.buf.partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise ConnectionError(head[:200].decode('utf-8', 'replace'))

    def send(self, text):
        payload = text.encode()
        n = len(payload)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack('!H', n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack('!Q', n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _frame(self):
        b = self.buf
        if len(b) < 2:
            return None
        fin, opcode, masked, n, at = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x80, b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, at = struct.unpack('!H', b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, at = struct.unpack('!Q', b[2:10])[0], 10
        mask = b[at:at + 4] if masked else None
        at += 4 if masked else 0
        if len(b) < at + n:
            return None
        payload = b[at:at + n]
        if mask:
            payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
        self.buf = b[at + n:]
        return fin, opcode, payload

    def recv(self, timeout):
        """Next text message, or None after `timeout` seconds. Fragmented messages are reassembled."""
        end = time.monotonic() + timeout
        parts = []
        while True:
            frame = self._frame()
            if frame is None:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self.sock.settimeout(left)
                try:
                    chunk = self.sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    return None
                if not chunk:
                    raise ConnectionError('socket closed')
                self.buf += chunk
                continue
            fin, opcode, payload = frame
            if opcode == 0x9:
                self.sock.sendall(bytes([0x8A, 0x80]) + b'\0\0\0\0')
            elif opcode == 0x8:
                raise ConnectionError('server closed the websocket')
            elif opcode in (0x1, 0x0):
                parts.append(payload)
                if fin:
                    return b''.join(parts).decode()


class Rpc:
    def __init__(self, ws, events_path=None):
        self.ws, self.n = ws, 0
        self.events = open(events_path, 'a', encoding='utf-8') if events_path else None

    def call(self, method, params, timeout=60, on_message=None):
        self.n += 1
        rid = self.n
        self.ws.send(json.dumps({'id': rid, 'method': method, 'params': params}, ensure_ascii=False))
        return self.until(lambda m: m.get('id') == rid and 'method' not in m, timeout, on_message)

    def notify(self, method, params=None):
        self.ws.send(json.dumps({'method': method, **({'params': params} if params is not None else {})}))

    def until(self, done, timeout, on_message=None):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            line = self.ws.recv(max(0.05, end - time.monotonic()))
            if line is None:
                continue
            if self.events:
                self.events.write(line + '\n')
                self.events.flush()
            msg = json.loads(line)
            if 'method' in msg and 'id' in msg:
                # A server request (approval, user input, elicitation). Nothing is auto-approved here:
                # declined means Codex treats it as refused and reports back in the thread.
                self.ws.send(json.dumps({'id': msg['id'], 'error': {'code': -32000, 'message': 'declined by delegate-to-codex'}}))
                if on_message:
                    on_message(msg)
                continue
            if on_message:
                on_message(msg)
            if done(msg):
                if 'error' in msg:
                    raise RuntimeError(json.dumps(msg['error'], ensure_ascii=False)[:500])
                return msg
        raise TimeoutError(f'no response for {timeout}s')

    def close(self):
        try:
            self.ws.sock.close()
        finally:
            if self.events:
                self.events.close()


def ensure_daemon():
    """Since ChatGPT.app 26.1103 (codex-cli 0.167) the app no longer keeps the managed app-server daemon
    alive; `codex app-server daemon start` is idempotent and brings it (and the control socket) back."""
    if os.path.exists(SOCKET):
        return
    if not os.path.exists(APP_CODEX):
        print(f'codex app-server daemon socket not found and no CLI at {APP_CODEX}', file=sys.stderr)
        sys.exit(2)
    print('codex app-server daemon socket not found; starting it via `codex app-server daemon start`', file=sys.stderr)
    r = subprocess.run([APP_CODEX, 'app-server', 'daemon', 'start'], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(f'daemon start failed (exit {r.returncode}): {r.stderr.strip()[-800:]}', file=sys.stderr)
        sys.exit(2)
    for _ in range(60):
        if os.path.exists(SOCKET):
            return
        time.sleep(0.5)
    print('daemon start reported success but the control socket never appeared', file=sys.stderr)
    sys.exit(2)


def connect(events_path=None):
    ensure_daemon()
    try:
        rpc = Rpc(WebSocket(SOCKET), events_path)
    except (OSError, ConnectionError) as error:
        print(f'cannot connect to codex app-server: {error}', file=sys.stderr)
        sys.exit(2)
    rpc.call('initialize', {'clientInfo': CLIENT, 'capabilities': {'experimentalApi': True}})
    rpc.notify('initialized')
    return rpc


def project_for(rpc, cwd):
    """(id, name) of the project with a root equal to cwd, else the deepest root containing it."""
    target = os.path.realpath(cwd)
    best = (None, None, -1, False)
    for project in (rpc.call('project/list', {}).get('result') or {}).get('data') or []:
        for root in project.get('roots') or []:
            path = os.path.realpath(root.get('path') or '')
            if not path:
                continue
            exact = target == path
            if exact or target.startswith(path + os.sep):
                if len(path) > best[2]:
                    best = (project['id'], project.get('name'), len(path), exact)
    return best[0], best[1], best[3]


def configured_mcp_servers(path=os.path.expanduser('~/.codex/config.toml')):
    try:
        import tomllib
        with open(path, 'rb') as fh:
            return sorted((tomllib.load(fh).get('mcp_servers') or {}).keys())   # names only, never args or env
    except Exception:
        return []


def cmd_projects(args):
    rpc = connect()
    for p in (rpc.call('project/list', {}).get('result') or {}).get('data') or []:
        print(p['id'], p['name'], json.dumps([r.get('path') for r in p.get('roots') or []], ensure_ascii=False))
    rpc.close()


def card_mandate(path):
    """The card's "## 授权" section is the Mandate: the only place dispatch parameters come from (dsh-superagent,
    2026-10-31: the envelope is declared once, immutable; widening it means a new card). Lines parsed:
      - 网络：是|否            - 额外可写目录：无 | `dir` …        - MCP：关 | 开（关闭 a、b）
      - 模型：默认 | <model>   - 强度：默认 | minimal|low|medium|high|xhigh（理由：…）
    Missing section → empty mandate (defaults)."""
    import re
    m = {'network': False, 'writable': [], 'mcp': False, 'disable_mcp': [], 'model': None, 'effort': None}
    try:
        text = open(path, encoding='utf-8').read()
    except OSError:
        return m
    sec = re.search(r'^##\s*授权.*?$(.*?)(?=^##\s|\Z)', text, re.M | re.S)
    if not sec:
        return m
    sec = sec.group(1)
    g = lambda k: (re.search(rf'^\s*-\s*{k}[:：]\s*(.+)$', sec, re.M) or [None, ''])[1].strip()
    m['network'] = g('网络').startswith('是')
    m['writable'] = re.findall(r'`([^`]+)`', g('额外可写目录'))
    mcp = g('MCP')
    m['mcp'] = mcp.startswith('开')
    m['disable_mcp'] = [x.strip() for x in re.findall(r'关闭\s*([^）);；]+)', mcp) for x in re.split('[、,，]', x) if x.strip()]
    model = g('模型'); effort = g('强度')
    m['model'] = None if (not model or model.startswith('默认')) else model.split('（')[0].split('(')[0].strip()
    e = effort.split('（')[0].split('(')[0].strip()
    m['effort'] = None if (not e or e.startswith('默认')) else e
    return m


def cmd_start(args):
    cwd = os.path.realpath(args.cwd)
    if args.card:
        # Card beats flags: a flag the card does not grant is refused, so a task session cannot widen the mandate.
        md = card_mandate(args.card)
        for flag, granted, what in ((args.network, md['network'], '--network'), (bool(args.writable), bool(md['writable']), '--writable'),
                                    (args.mcp, md['mcp'], '--mcp')):
            if flag and not granted:
                sys.exit(f'{what} 未在任务卡“## 授权”里授予；改卡再派，不要在派发时放宽')
        args.network = md['network']
        args.writable = md['writable'] or None
        args.mcp = md['mcp']
        args.disable_mcp = sorted(set((args.disable_mcp or []) + md['disable_mcp']))
        if md['model']:
            args.model = md['model']
        if md['effort']:
            args.effort = md['effort']
        if args.mcp:
            # Live-trading and outbound-messaging servers are never granted to a delegated thread (global boundary).
            args.disable_mcp = sorted(set(args.disable_mcp) | (NEVER_MCP & set(configured_mcp_servers())))
    dispatch_caller = None
    if args.card:
        # One card, one fresh Claude Code context: a context that already started another card is refused.
        import dispatch_scope
        refusal, dispatch_caller = dispatch_scope.check(os.path.join(EVIDENCE_ROOT, '_threads'), args.card)
        if refusal:
            sys.exit(refusal)
    rpc = connect()
    project_id, project_name, exact = project_for(rpc, cwd)
    if project_id is None:
        print(f'warning: {cwd} is not under any Codex project; the thread will only show in Recents', file=sys.stderr)
    elif not exact:
        print(f'warning: {cwd} is inside project {project_name!r} but is not one of its roots; '
              'the thread will only show in Recents. Pass the repository root as --cwd.', file=sys.stderr)
    params = {'cwd': cwd, 'approvalPolicy': args.approval, 'sandbox': args.sandbox,
              'threadSource': 'agent_created_thread'}
    if project_id:
        params['projectId'] = project_id
    if args.model:
        params['model'] = args.model
    config = {}
    # Hooks stay off unless the thread is registered for the end-of-turn self-audit (--card/--worktree/--base):
    # then only ~/.codex/hooks.json's Stop hook (codex_stop_audit.py) applies; plugins stay off, so plugin
    # hooks do not load.
    hooks_on = bool(args.card and args.worktree and args.base)
    if not args.mcp:
        # MCP servers, plugins, apps and hooks bypass command approval and the sandbox; a delegated
        # thread gets none of them unless asked. Dotted keys per server name; names come from the
        # user's config.toml because an unknown name makes thread/start fail.
        config.update({f'mcp_servers.{name}.enabled': False for name in configured_mcp_servers()})
        config.update({'features.plugins': False, 'features.apps': False, 'features.hooks': hooks_on})
    else:
        # --mcp keeps plugins and apps (e.g. the browser plugin) on, and named servers can be
        # switched off one by one (e.g. a server that reaches live trading or sends messages; see CODEX_NEVER_MCP).
        config['features.hooks'] = hooks_on
        known = set(configured_mcp_servers())
        unknown = sorted(set(args.disable_mcp or []) - known)
        if unknown:
            sys.exit(f'unknown MCP server(s) {unknown}; configured: {sorted(known)}')
        config.update({f'mcp_servers.{name}.enabled': False for name in args.disable_mcp or []})
    sww = {}
    if args.network:
        sww['network_access'] = True
    if args.writable:
        # Extra writable directories outside the repository root (e.g. ~/.docker for image builds, a sibling
        # workspace). Explicit and recorded instead of sessions improvising their own RPC calls.
        sww['writable_roots'] = [os.path.realpath(os.path.expanduser(d)) for d in args.writable]
    if sww:
        config['sandbox_workspace_write'] = sww
    if args.effort:
        config['model_reasoning_effort'] = args.effort
    if config:
        params['config'] = config
    started = rpc.call('thread/start', params)['result']
    thread = started['thread']
    rpc.call('thread/name/set', {'threadId': thread['id'], 'name': args.name})
    rpc.call('thread/unsubscribe', {'threadId': thread['id']})
    rpc.close()
    if hooks_on:
        # Registration the Stop hook looks up by CODEX_THREAD_ID; threads without one are ignored by the hook.
        reg_dir = os.path.join(EVIDENCE_ROOT, '_threads')
        os.makedirs(reg_dir, exist_ok=True)
        with open(os.path.join(reg_dir, f"{thread['id']}.json"), 'w', encoding='utf-8') as fh:
            json.dump({'worktree': os.path.realpath(args.worktree), 'card': os.path.realpath(args.card),
                       'base': args.base, 'cwd': cwd, 'name': args.name, 'blocks': 0,
                       'caller': dispatch_caller}, fh, ensure_ascii=False)
        try:
            # Environment ledger (2026-11-10): the worktree was opened by task_worktree.py; attach the thread and card so
            # workspace_hygiene.py can tell who borrowed it and what returns it. Worktrees opened before the ledger existed
            # get a record here, owner codex-delegate, return on card accept.
            import env_ledger
            key = env_ledger.key_for_worktree(args.worktree)
            if env_ledger.attach(key, thread=thread['id'], card=os.path.realpath(args.card)) is None:
                env_ledger.open_env(key, kind='worktree', repo=cwd, owner='codex-delegate', task=os.path.basename(key),
                                    return_condition='card_accepted', thread=thread['id'], card=os.path.realpath(args.card))
        except Exception as error:  # the ledger never blocks a dispatch
            print(f'warning: environment ledger not updated: {error}', file=sys.stderr)
    print(json.dumps({'threadId': thread['id'], 'projectId': project_id, 'project': project_name,
                      'stopAudit': hooks_on, 'card': os.path.realpath(args.card) if args.card else None,
                      'worktree': os.path.realpath(args.worktree) if args.worktree else None, 'base': args.base,
                      'mandate': {'network': bool(args.network), 'writable': args.writable or [], 'mcp': bool(args.mcp),
                                  'disable_mcp': args.disable_mcp or []},
                      'cwd': cwd, 'name': args.name, 'model': started.get('model'),
                      'effort': started.get('reasoningEffort'), 'sandbox': started.get('sandbox'),
                      'rolloutPath': thread.get('path')}, ensure_ascii=False))


def cmd_turn(args):
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        print('empty prompt', file=sys.stderr)
        sys.exit(1)
    import dispatch_scope
    refusal = dispatch_scope.check_turn(os.path.join(EVIDENCE_ROOT, '_threads'), args.thread)
    if refusal:
        sys.exit(refusal)
    rpc = connect(args.events)
    rpc.call('thread/resume', {'threadId': args.thread, 'excludeTurns': True})
    turn_params = {'threadId': args.thread, 'input': [{'type': 'text', 'text': prompt}]}
    rpc.call('turn/start', turn_params)
    state = {'last': None, 'declined': 0, 'items': 0}

    def watch(msg):
        method, params = msg.get('method'), msg.get('params') or {}
        if 'id' in msg and method:
            state['declined'] += 1
            print(f'declined server request: {method}', file=sys.stderr)
        if method == 'item/completed':
            item = params.get('item') or {}
            state['items'] += 1
            if item.get('type') == 'agentMessage':
                state['last'] = item.get('text')

    def finished(msg):
        return msg.get('method') == 'turn/completed' and (msg.get('params') or {}).get('threadId') == args.thread

    try:
        final = rpc.until(finished, args.timeout, watch)
        # TurnStatus: completed | interrupted | failed | inProgress
        status = ((final.get('params') or {}).get('turn') or {}).get('status') or 'unknown'
    except TimeoutError:
        status = 'timeout'
        try:
            rpc.call('turn/interrupt', {'threadId': args.thread}, 30)
        except Exception:
            pass
    finally:
        try:
            rpc.call('thread/unsubscribe', {'threadId': args.thread}, 30)
        except Exception:
            pass
        rpc.close()
    if args.last_message and state['last'] is not None:
        with open(args.last_message, 'w', encoding='utf-8') as fh:
            fh.write(state['last'])
    print(json.dumps({'threadId': args.thread, 'status': status, 'items': state['items'],
                      'declinedRequests': state['declined'], 'lastMessage': state['last']}, ensure_ascii=False))
    sys.exit(0 if status == 'completed' and state['declined'] == 0 else 3)


def cmd_interrupt(args):
    rpc = connect()
    rpc.call('turn/interrupt', {'threadId': args.thread})
    rpc.close()
    print('interrupted', args.thread)


def cmd_archive(args):
    rpc = connect()
    rpc.call('thread/archive', {'threadId': args.thread})
    rpc.close()
    print('archived', args.thread)


def cmd_refresh(args):
    """The app only re-reads its thread list when it switches project; its own CLI can drive that.
    Opening the same project again is not enough (verified 2026-10-24), so hop to another project root first."""
    if not os.path.exists(APP_CODEX):
        print(f'bundled codex binary not found at {APP_CODEX}', file=sys.stderr)
        sys.exit(2)
    target = os.path.realpath(args.cwd)
    rpc = connect()
    roots = [os.path.realpath(r.get('path') or '') for p in (rpc.call('project/list', {}).get('result') or {}).get('data') or []
             for r in p.get('roots') or []]
    rpc.close()
    hop = next((r for r in roots if r and r != target and os.path.isdir(r)), None)
    for path in ([hop] if hop else []) + [target]:
        subprocess.run([APP_CODEX, 'app', path], check=False, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    print('asked the Codex app to reopen', target, f'(via {hop})' if hop else '')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('projects').set_defaults(fn=cmd_projects)
    s = sub.add_parser('start')
    s.add_argument('--cwd', required=True, help='repository root (must equal a Codex project root to be filed under it)')
    s.add_argument('--name', required=True)
    s.add_argument('--sandbox', default='workspace-write', choices=('read-only', 'workspace-write', 'danger-full-access'))
    s.add_argument('--approval', default='never', choices=('never', 'on-request', 'untrusted'))
    #
    s.add_argument('--model', default='gpt-6-astra')
    s.add_argument('--effort', default='xhigh', choices=('minimal', 'low', 'medium', 'high', 'xhigh'))
    s.add_argument('--network', action='store_true', help='allow network inside the workspace-write sandbox')
    s.add_argument('--writable', action='append', metavar='DIR', help='extra writable directory (repeatable); say why in the task card')
    s.add_argument('--mcp', action='store_true', help='keep the user\'s MCP servers, plugins and apps on (off by default)')
    s.add_argument('--disable-mcp', action='append', metavar='NAME', help='with --mcp: switch off this configured MCP server (repeatable)')
    s.add_argument('--worktree', help='task worktree; with --card and --base, registers the thread for the Stop-hook self-audit')
    s.add_argument('--card', help='task card README.md (absolute path)')
    s.add_argument('--base', help='commit the task started from (parent of the task-card commit)')
    s.set_defaults(fn=cmd_start)
    t = sub.add_parser('turn')
    t.add_argument('--thread', required=True)
    t.add_argument('--events', required=True, help='JSONL file that receives every server message')
    t.add_argument('--prompt', help='turn text; read from stdin when omitted')
    t.add_argument('--last-message', help='file that receives the final agent message')
    t.add_argument('--timeout', type=int, default=7200)
    t.set_defaults(fn=cmd_turn)
    for name, fn in (('interrupt', cmd_interrupt), ('archive', cmd_archive)):
        p = sub.add_parser(name)
        p.add_argument('--thread', required=True)
        p.set_defaults(fn=fn)
    r = sub.add_parser('refresh')
    r.add_argument('--cwd', required=True)
    r.set_defaults(fn=cmd_refresh)
    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
