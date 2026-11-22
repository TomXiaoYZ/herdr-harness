#!/usr/bin/env python3
"""Summarize how delegated Codex tasks went, as material for harness/skill improvements.

Reads only local evidence: the delegation evidence folders, task cards' round notes, and the Codex
state database (thread metadata; never transcripts or credentials). Prints Markdown.

  review_delegations.py [--since YYYY-MM-DD] [--evidence ~/.agents/work-plans/evidence/delegate]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3

STATE_DB = os.path.expanduser('~/.codex/state_5.sqlite')
CC_PROJECTS = os.path.expanduser('~/.claude/projects')
WINDOW_PAD = 10 * 60   # seconds around a task's evidence files that still count as that task's CC work
DEFAULT_EVIDENCE = os.path.expanduser('~/.agents/work-plans/evidence/delegate')
RE_STATUS = re.compile(r'^\s*status:\s*(\w+)', re.M)
RE_BLOCKERS = re.compile(r'blockers:\s*\n((?:\s+-\s.*\n?)+)', re.M)
RE_HUMAN = re.compile(r'^\s*human_action:\s*(.+)$', re.M)
RE_VERDICT = re.compile(r'verdict:\s*(accept|return|blocked)')  # verdicts also appear inline: “重派判决（…）：verdict: accept｜final: false”
RE_FINAL = re.compile(r'final:\s*true')
PROPOSALS = os.path.join(DEFAULT_EVIDENCE, '_proposals')
MODE_FILE = os.path.expanduser('~/.agents/work-plans/continuation-mode.json')


def continuation_mode(repo):
    try:
        return json.load(open(MODE_FILE, encoding='utf-8')).get(repo, 'observe')
    except (OSError, ValueError):
        return 'observe'
STALE_HOURS = 6


def read(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return ''


def summarize_events(path, worktree):
    """Counts from a turn's JSONL event stream: commands, file changes, writes outside the worktree,
    server requests (which the skill always declines), sub-agent spawns."""
    n = {'commands': 0, 'file_changes': 0, 'outside_writes': 0, 'server_requests': 0, 'subagents': 0}
    outside = []
    try:
        for line in open(path, encoding='utf-8'):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            meth = m.get('method')
            p = m.get('params') or {}
            item = p.get('item') or {}
            if 'id' in m and meth:
                n['server_requests'] += 1
            if meth == 'item/started' and item.get('type') == 'commandExecution':
                n['commands'] += 1
            if meth == 'item/completed' and item.get('type') == 'fileChange':
                for c in item.get('changes') or []:
                    n['file_changes'] += 1
                    pth = c.get('path') or ''
                    if worktree and not pth.startswith(worktree.rstrip('/') + '/'):
                        n['outside_writes'] += 1
                        outside.append(pth)
            if meth == 'thread/started' and 'subagent' in json.dumps(p.get('thread', {}).get('source', '')):
                n['subagents'] += 1
    except OSError:
        pass
    return n, outside


def first_thread_id(path):
    """Main thread id of a run: the thread of its first turn/started event."""
    try:
        for line in open(path, encoding='utf-8'):
            if '"turn/started"' in line:
                return (json.loads(line).get('params') or {}).get('threadId', '')
    except (OSError, ValueError):
        pass
    return ''


def thread_meta(thread_id):
    try:
        db = sqlite3.connect(STATE_DB)
        row = db.execute("select created_at, updated_at, archived, cwd, model, reasoning_effort, tokens_used from threads where id=?",
                         (thread_id,)).fetchone()
        sub = db.execute("select count(*) from threads where source like ?", (f'%{thread_id}%',)).fetchone()[0]
        roots = {os.path.realpath(r[0]) for r in db.execute("select path from project_roots")}
        db.close()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    created, updated, archived, cwd, model, effort, tokens = row
    # The Codex app files a thread under a project when its cwd equals one of the project roots (verified 2026-10-24).
    in_project = os.path.realpath(cwd or '') in roots
    return {'minutes': round((updated - created) / 60, 1) if created and updated else None, 'archived': bool(archived),
            'project': in_project, 'model': model, 'effort': effort, 'tokens': tokens, 'subagents': sub}


def cc_slug(path):
    """Claude Code's per-project transcript folder name for a working directory."""
    return re.sub(r'[^A-Za-z0-9-]', '-', os.path.realpath(path))


def cc_tokens(repo_root, start, end):
    """Sum Claude Code usage (input + cache, output) of assistant messages in [start, end] across every
    transcript of the repository's project sessions, task sessions and worktree sessions (subagents included).
    Timestamps in transcripts are UTC ISO strings. Returns (input_plus_cache, output, messages)."""
    slug = cc_slug(repo_root)
    inp = out = n = 0
    folders = [d for d in glob.glob(os.path.join(CC_PROJECTS, slug + '*')) if os.path.isdir(d)]
    for folder in folders:
        for path in glob.glob(os.path.join(folder, '**', '*.jsonl'), recursive=True):
            try:
                if os.path.getmtime(path) < start:
                    continue
                for line in open(path, encoding='utf-8'):
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    ts = m.get('timestamp')
                    if not ts:
                        continue
                    t = dt.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                    if not (start <= t <= end):
                        continue
                    u = (m.get('message') or {}).get('usage') or {}
                    inp += u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0) + u.get('cache_read_input_tokens', 0)
                    out += u.get('output_tokens', 0)
                    n += 1
            except OSError:
                continue
    return inp, out, n


def task_window(folder):
    files = [p for p in glob.glob(os.path.join(folder, '*')) if os.path.isfile(p)]
    if not files:
        return None, None
    times = [os.path.getmtime(p) for p in files]
    return min(times) - WINDOW_PAD, max(times) + WINDOW_PAD


def card_rounds(card_path):
    """Verdicts the task sessions appended under "### 第 N 轮…": [(round, verdict, final)].

    The heading may carry a suffix ("### 第 1 轮判决（Claude Code，2026-11-13）"); 2026-11-15 the bare-heading-only
    pattern missed such verdicts and left an accepted card in "等用户回答"."""
    text = read(card_path)
    out = []
    for m in re.finditer(r'^###\s*第\s*(\d+)\s*轮[^\n]*$(.*?)(?=^###\s|\Z)', text, re.M | re.S):
        vs = RE_VERDICT.findall(m.group(2))
        if vs:  # the last verdict written under the round wins (a re-dispatch after a daemon failure overrides)
            out.append((int(m.group(1)), vs[-1], bool(RE_FINAL.search(m.group(2)))))
    return out


def derive_stage(runs, receipts, verdicts, newest_mtime, returned=False):
    """dsh-superagent's rule (docs/003): one stage per open ticket, decided by what it waits for; first matching row
    wins, from structured fields only (receipt status/human_action, verdict/final), never from free text.
    Returns (stage, label, who) with who in {'user', 'CC', 'Codex', '-'}."""
    last_receipt = receipts[-1] if receipts else None
    last_verdict = verdicts[-1] if verdicts else None
    n_runs, n_receipts = len(runs), len(receipts)
    # A verdict on the latest round outranks the receipt's human_action: the task session answered it (2026-11-13,
    # collector-mid-anchor-18 sat in "等用户回答" a week after accept).
    if last_verdict and last_verdict[0] >= n_runs and last_verdict[1] == 'accept':
        if returned:
            return 'ended', '已验收并归还', '-'
        return 'person/review', '已验收：等提交/PR/合入并归还工作树', 'CC'
    if last_receipt and n_receipts == n_runs and not (last_verdict and last_verdict[0] >= n_runs):
        st0 = RE_STATUS.search(last_receipt)
        if st0 and st0.group(1) == 'failed':
            return 'person/blocked', 'Codex 报 failed（多为派发或守护进程失败）：等重派或用户', 'CC'
        h = RE_HUMAN.search(last_receipt)
        if h and h.group(1).strip().lower() not in ('none', '无', '-'):
            return 'person/answer', '等用户回答', 'user'
    # Only a verdict on the latest round decides who waits: an answered round-1 "blocked" must not hold round 2
    # (2026-11-15, v2-g3-a1-watchlist showed "等用户回答" after its round-2 receipt arrived).
    current = last_verdict if last_verdict and last_verdict[0] >= n_runs else None
    if current and current[1] == 'blocked':
        return 'person/answer', '等用户回答', 'user'
    if current and current[1] == 'return' and current[2]:
        return 'person/split', '第 2 轮仍退回：等拆卡或用户决定', 'user'
    if last_verdict and last_verdict[1] == 'accept':  # any round: a re-dispatch adds run files without a new round
        return 'person/review', '已验收：等提交/PR/合入', 'CC'
    if n_runs > n_receipts:
        hours = (dt.datetime.now().timestamp() - newest_mtime) / 3600
        return ('working/stale', f'执行中无回执已 {hours:.0f} 小时：查线程或 interrupt', 'Codex') if hours > STALE_HOURS else ('working', '执行中', 'Codex')
    if last_receipt and n_receipts == n_runs:
        st = RE_STATUS.search(last_receipt)
        status = st.group(1) if st else '?'
        if status in ('blocked', 'failed') and (not last_verdict or last_verdict[0] < n_runs):
            return 'person/blocked', f'Codex 报 {status}：等验收判决或用户', 'CC'
        if not last_verdict or last_verdict[0] < n_runs:
            if (dt.datetime.now().timestamp() - newest_mtime) > 2 * 24 * 3600 and not last_verdict:
                return 'ended', '已结束（10-30 前旧流程，卡内无判决）', '-'
            return 'acceptance', '回执已到：等任务会话验收', 'CC'
        if last_verdict[1] == 'return':
            return 'working/queued', '已退回：等派第 ' + str(last_verdict[0] + 1) + ' 轮', 'CC'
    return 'ended', '已结束', '-'


def list_proposals(since):
    """Observe-mode records: cards a project session would have dispatched on its own initiative but only recorded
    (dsh-superagent eval.mode=observe → would_accept). One Markdown file per proposal under _proposals/<repo>/."""
    out = []
    for path in sorted(glob.glob(os.path.join(PROPOSALS, '*', '*.md'))):
        if os.path.getmtime(path) < since:
            continue
        text = read(path)
        title = (re.search(r'^#\s*(.+)$', text, re.M) or [None, os.path.basename(path)])[1]
        why = (re.search(r'^\s*-?\s*(?:理由|why)[:：]\s*(.+)$', text, re.M) or [None, ''])[1]
        repo = path.split(os.sep)[-2]
        out.append((f"{repo}[{continuation_mode(repo)}]", title.strip(), why.strip()))
    return out


def fmt_m(n):
    return f'{n / 1e6:.1f}M' if n >= 1e5 else f'{n / 1e3:.0f}K'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--since', help='only tasks whose evidence folder changed on/after this date (YYYY-MM-DD)')
    ap.add_argument('--evidence', default=DEFAULT_EVIDENCE)
    ap.add_argument('--no-cost', action='store_true', help='skip the per-task CC token scan (slow on big transcript folders)')
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since).timestamp() if a.since else 0

    rows, findings, needs_tom = [], [], []
    # Evidence layouts drift between project sessions (turn-N.out vs turn-N.log, run-A2.jsonl, no start.json),
    # so a task is any folder holding at least one run-*.jsonl; rounds are counted from those files.
    folders = sorted({os.path.dirname(p) for p in glob.glob(os.path.join(a.evidence, '*', '*', 'run-*.jsonl'))})
    for folder in folders:
        if os.path.getmtime(folder) < since:
            continue
        repo, task = folder.split(os.sep)[-2:]
        meta = {}
        try:
            meta = json.load(open(os.path.join(folder, 'start.json'), encoding='utf-8'))
        except (OSError, ValueError):
            pass
        tid = meta.get('threadId') or read(os.path.join(folder, 'thread-id')).strip()
        worktree = meta.get('worktree') or read(os.path.join(folder, 'worktree')).strip()
        runs = sorted(glob.glob(os.path.join(folder, 'run-*.jsonl')), key=os.path.getmtime)
        statuses, declined, blockers, lasts = [], 0, [], []
        totals = {'commands': 0, 'file_changes': 0, 'outside_writes': 0, 'server_requests': 0}
        for ev in runs:
            suffix = os.path.basename(ev)[len('run-'):-len('.jsonl')]
            last, j = '', {}
            for name in (f'turn-{suffix}.out', f'turn-{suffix}.log'):
                out = read(os.path.join(folder, name))
                if out:
                    try:
                        j = json.loads(out.strip().splitlines()[0])
                    except (ValueError, IndexError):
                        j = {}
                    break
            last = j.get('lastMessage') or read(os.path.join(folder, f'receipt-{suffix}.md'))
            lasts.append(last)
            tid = tid or j.get('threadId') or first_thread_id(ev)
            declined += j.get('declinedRequests', 0) or 0
            m = RE_STATUS.search(last)
            statuses.append(m.group(1) if m else (j.get('status') or ('?' if not last else 'no-status')))
            b = RE_BLOCKERS.search(last)
            if b:
                for line in b.group(1).splitlines():
                    line = line.strip(' -')
                    if line and line != 'none':
                        blockers.append(line)
            n, outside = summarize_events(ev, worktree)
            for k in totals:
                totals[k] += n[k]
            if outside:
                findings.append(f'{repo}/{task}: 工作树外写入 {len(outside)} 处，例如 `{outside[0]}`')
        tm = thread_meta(tid) if tid else {}
        # Card lookup: start.json `card` → the worktree → the main checkout (the worktree is removed once the task merges,
        # one repository, 2026-10-31). The card must still exist; otherwise the stage is "card missing", not "awaiting".
        card = meta.get('card') or ''
        if card and not os.path.exists(card):
            card = ''
        repo_root_for_card = meta.get('cwd') or (worktree.split('/.worktrees/')[0] if '/.worktrees/' in worktree else '')
        for base in [b for b in (worktree, repo_root_for_card) if b and os.path.isdir(b)]:
            if card:
                break
            cands = sorted(glob.glob(os.path.join(base, 'docs', '**', 'README.md'), recursive=True), key=os.path.getmtime)
            cands = [c for c in cands if '.worktrees' not in c[len(base):] or base == worktree]
            named = [c for c in cands if task.split('-2026')[0] in c or task in c]
            card = next((c for c in reversed(named or cands) if '## 轮次' in read(c) and (named or task in read(c))), '')
        receipts = [r for r in lasts if r]
        files = [p for p in glob.glob(os.path.join(folder, '*')) if os.path.isfile(p)]
        newest = max(os.path.getmtime(p) for p in files)
        returned = bool(worktree) and not os.path.isdir(worktree)
        stage, label, who = derive_stage(runs, receipts, card_rounds(card) if card else [], newest, returned)
        if not card and stage not in ('working', 'working/stale'):
            if (dt.datetime.now().timestamp() - newest) > 2 * 24 * 3600:
                stage, label, who = 'ended', '已结束（卡与工作树已归还）', '-'
            else:
                stage, label, who = 'unknown/no-card', '找不到任务卡（start.json 无 card，工作树已删）：无法推导', '-'
        if who == 'user':
            needs_tom.append((repo, task, label))
        if stage == 'working/stale':
            findings.append(f'{repo}/{task}: {label}')
        if stage == 'person/review' and worktree and os.path.isdir(worktree):
            # Environment rule (2026-11-10): accept + merged means the worktree is returned; one still on disk is unfinished closure.
            findings.append(f'{repo}/{task}: 已 accept 但工作树还在 `{worktree}`；合入后 `task_worktree.py return --worktree` 归还')
        repo_root = meta.get('cwd') or (worktree.split('/.worktrees/')[0] if '/.worktrees/' in worktree else '')
        cc_in = cc_out = None
        if not a.no_cost and repo_root:
            w0, w1 = task_window(folder)
            if w0:
                cc_in, cc_out, _ = cc_tokens(repo_root, w0, w1)
        if tm and not tm.get('project', True) and meta.get('channel') != 'herdr':  # herdr sessions run in the worktree, not an App project
            findings.append(f'{repo}/{task}: 线程没有归到 Codex 项目')
        if declined:
            findings.append(f'{repo}/{task}: {declined} 个服务端请求被拒（审批/提问），说明任务卡或策略留了口子')
        if statuses and statuses[-1] == '?':
            findings.append(f'{repo}/{task}: 最后一轮没有回执（仍在运行、被中断或回执未落盘）')
        if len(runs) > 1:
            findings.append(f'{repo}/{task}: 用了 {len(runs)} 轮，阻塞原因：' + ('；'.join(blockers[:3]) or '回执未写 blockers，需读任务卡“轮次”'))
        if len(runs) > 2:
            findings.append(f'{repo}/{task}: 超过 2 轮（2026-10-30 起不派第 3 轮，应拆卡或交用户）')
        if cc_in and tm.get('tokens') and cc_in > 5 * tm['tokens']:
            findings.append(f'{repo}/{task}: CC 侧 token（{fmt_m(cc_in)}）超过 Codex（{fmt_m(tm["tokens"])}）5 倍，看项目会话是否自己在读日志或跑构建')
        rows.append((repo, task, label, len(runs), '→'.join(statuses) or '-', totals['commands'], totals['file_changes'],
                     tm.get('minutes'), tm.get('subagents', 0), tm.get('model') or '-', tm.get('effort') or '-',
                     fmt_m(cc_in) if cc_in is not None else '-', fmt_m(cc_out) if cc_out is not None else '-',
                     fmt_m(tm['tokens']) if tm.get('tokens') else '-'))
        for b in blockers:
            findings.append(f'{repo}/{task} 阻塞：{b}')
    names = {}
    for r in rows:
        names.setdefault(r[0].replace('.nosync', ''), set()).add(r[0])
    for base, variants in names.items():
        if len(variants) > 1:
            findings.append(f'证据目录的仓库名不统一：{sorted(variants)}（应为仓库根目录名）')

    print(f'# 派发复盘素材（生成于 {dt.datetime.now():%Y-%m-%d %H:%M}）\n')
    print('## 需要用户的卡\n')
    if not needs_tom:
        print('- 无')
    for repo, task, label in needs_tom:
        print(f'- {repo}/{task}：{label}')
    proposals = list_proposals(since)
    print('\n## 未派发的提案（按仓库模式：auto 仓库应自行拆卡派发，observe 仓库只记录）\n')
    if not proposals:
        print('- 无')
    for repo, title, why in proposals:
        print(f'- {repo}：{title}' + (f'（{why}）' if why else ''))
    print('\n## 全部任务\n')
    print('| 仓库 | 任务 | 阶段 | 轮数 | 状态链 | 命令 | 文件改动 | 线程分钟 | 子代理 | 模型 | 强度 | CC 输入+缓存 | CC 输出 | Codex |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in rows:
        print('| ' + ' | '.join(str(x) if x is not None else '-' for x in r) + ' |')
    print('\nCC 列：该仓库所有 Claude Code 转录（项目会话、任务会话、工作树会话、子代理）在任务证据时间窗内的用量；'
          '每个被接受改动的成本 = 该行 CC 与 Codex 之和，复盘按这个比，不比总量。\n')
    print('\n## 值得看的信号\n')
    if not findings:
        print('- 无：所有任务一轮完成、无阻塞、无越界写入、无被拒请求。')
    for f in findings:
        print(f'- {f}')
    print('\n## 人工补充来源（脚本不读）\n')
    print('- 各项目 CC 会话里用户的纠正与追问（用会话搜索：delegate-to-codex、任务卡、回执、blocked、红线）。')
    print('- 各仓库 `docs/tasks/*/README.md` 的“轮次”小节与验收记录。')
    print('- Codex 线程里 Codex 对任务卡的质疑或自行补的边界。')


if __name__ == '__main__':
    main()
