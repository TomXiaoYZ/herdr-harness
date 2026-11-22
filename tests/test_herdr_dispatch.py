import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'skills', 'delegate-to-codex', 'scripts')
sys.path.insert(0, SCRIPTS)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hd = load('herdr_dispatch', os.path.join(SCRIPTS, 'herdr_dispatch.py'))
cc = load('herdr_cc_status', os.path.join(ROOT, 'scripts', 'herdr_cc_status.py'))

SERVERS = ['computer-use', 'broker', 'telegram']
hd.NEVER_MCP = {'broker', 'telegram'}   # as if CODEX_NEVER_MCP=broker,telegram


def mandate(**kw):
    m = {'network': False, 'writable': [], 'mcp': False, 'disable_mcp': [], 'model': None, 'effort': None}
    m.update(kw)
    return m


class CodexArgvTest(unittest.TestCase):
    def argv(self, m):
        with mock.patch.object(hd, 'configured_mcp_servers', return_value=SERVERS):
            return hd.codex_argv(m, 'PROMPT')

    def test_defaults_close_everything(self):
        a = self.argv(mandate())
        self.assertEqual(a[-1], 'PROMPT')
        self.assertIn('--no-daemon', a)
        self.assertEqual(a[a.index('-m') + 1], 'gpt-6-astra')
        self.assertIn('model_reasoning_effort="xhigh"', a)
        self.assertEqual(a[a.index('-s') + 1], 'workspace-write')
        self.assertEqual(a[a.index('-a') + 1], 'never')
        for name in SERVERS:
            self.assertIn(f'mcp_servers.{name}.enabled=false', a)
        self.assertIn('plugins', a)
        self.assertFalse(any('network_access' in x for x in a))

    def test_mandate_widens_only_what_it_grants(self):
        a = self.argv(mandate(network=True, writable=['/tmp/x'], model='m1', effort='high'))
        self.assertIn('sandbox_workspace_write.network_access=true', a)
        self.assertIn('sandbox_workspace_write.writable_roots=' + json.dumps([os.path.realpath('/tmp/x')]), a)
        self.assertEqual(a[a.index('-m') + 1], 'm1')
        self.assertIn('model_reasoning_effort="high"', a)

    def test_mcp_grant_still_closes_trading_and_messaging(self):
        a = self.argv(mandate(mcp=True))
        self.assertNotIn('mcp_servers.computer-use.enabled=false', a)
        self.assertIn('mcp_servers.broker.enabled=false', a)
        self.assertIn('mcp_servers.telegram.enabled=false', a)
        self.assertNotIn('plugins', a)


class LastTurnTest(unittest.TestCase):
    def test_reads_last_assistant_message_of_latest_turn(self):
        rows = [
            {'type': 'session_meta', 'payload': {'id': 's1', 'cwd': '/w'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': []}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                                  'content': [{'type': 'output_text', 'text': '回执'}]}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': []}},
        ]
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as fh:
            fh.write('\n'.join(json.dumps(r) for r in rows[:4]))
        self.assertEqual(hd.last_turn(fh.name), (True, '回执'))
        with open(fh.name, 'a') as out:
            out.write('\n' + json.dumps(rows[4]))
        self.assertEqual(hd.last_turn(fh.name), (False, None))
        os.unlink(fh.name)


class WaitEvidenceTest(unittest.TestCase):
    def test_wait_writes_codex_thread_layout(self):
        tmp = tempfile.mkdtemp()
        rollout = os.path.join(tmp, 'rollout.jsonl')
        with open(rollout, 'w') as fh:
            fh.write('\n'.join(json.dumps(r) for r in [
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                                      'content': [{'type': 'output_text', 'text': 'status: done'}]}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete'}}]))
        reg = {'cwd': '/r/repo', 'worktree': '/r/repo/.worktrees/t1', 'terminal': 'w1:t1', 'rollout': rollout}
        done = mock.Mock(stdout='{"status": "done"}\n', stderr='')
        with mock.patch.object(hd, 'reg_for', return_value=(os.path.join(tmp, 'sid9.json'), reg)), \
                mock.patch.object(hd, 'EVIDENCE_ROOT', tmp), mock.patch.object(hd.subprocess, 'run', return_value=done), \
                mock.patch('sys.stdout'), self.assertRaises(SystemExit) as ex:
            hd.cmd_wait(mock.Mock(name='t1', round=1, timeout=5))
        self.assertEqual(ex.exception.code, 0)
        ev = os.path.join(tmp, 'repo', 't1')
        self.assertEqual(open(os.path.join(ev, 'receipt-1.md')).read(), 'status: done')
        self.assertEqual(json.load(open(os.path.join(ev, 'run-1.jsonl')))['threadId'], 'sid9')
        self.assertEqual(json.load(open(os.path.join(ev, 'turn-1.out'))), {'status': 'completed', 'threadId': 'sid9'})


class WaitSliceTest(unittest.TestCase):
    def test_message_to_caller_does_not_end_the_wait(self):
        outs = [mock.Mock(stdout='{"reason": "message", "status": "working"}\n', stderr=''),
                mock.Mock(stdout='{"reason": "status", "status": "done"}\n', stderr='')]
        with mock.patch.object(hd.subprocess, 'run', side_effect=outs) as run:
            self.assertEqual(hd.hc_wait('t1', 60)['status'], 'done')
        self.assertEqual(run.call_count, 2)

    def test_still_working_exits_4_and_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        reg = {'cwd': '/r/repo', 'worktree': '/r/repo/.worktrees/t1', 'terminal': 'w1:t1', 'rollout': '/nonexistent'}
        working = mock.Mock(stdout='{"reason": "timeout", "status": "working"}\n', stderr='')
        with mock.patch.object(hd, 'reg_for', return_value=(os.path.join(tmp, 'sid9.json'), reg)), \
                mock.patch.object(hd, 'EVIDENCE_ROOT', tmp), mock.patch.object(hd.subprocess, 'run', return_value=working), \
                mock.patch('sys.stdout'), self.assertRaises(SystemExit) as ex:
            hd.cmd_wait(mock.Mock(name='t1', round=1, timeout=5))
        self.assertEqual(ex.exception.code, 4)
        self.assertFalse(os.path.exists(os.path.join(tmp, 'repo', 't1', 'receipt-1.md')))

    def test_default_slice_returns_before_claude_code_backgrounds_it(self):
        self.assertLess(hd.WAIT_SLICE, 180)


class ClaudeStatusTest(unittest.TestCase):
    def test_reports(self):
        self.assertEqual(cc.report({'hook_event_name': 'Stop'}), 'state=done:app=claude-code')
        self.assertEqual(cc.report({'hook_event_name': 'PermissionRequest'}),
                         'state=blocked:kind=permission:app=claude-code')
        self.assertEqual(cc.report({'hook_event_name': 'Notification', 'notification_type': 'idle_prompt'}), None)
        self.assertEqual(cc.report({'hook_event_name': 'Notification', 'notification_type': 'elicitation_dialog'}),
                         'state=blocked:kind=question:app=claude-code')


if __name__ == '__main__':
    unittest.main()
