#!/usr/bin/env python3
"""Strict-acceptance audit of a delegated task's diff against its task card.

Reports, as Markdown: files changed outside the card's expected list; added source vs test lines against the
card's budget; and every added line matching a defensive-coding pattern, with file:line, so Claude Code can
demand a boundary justification for each or send the round back.

  diff_audit.py --worktree <path> --base <commit> [--card <task card README.md>]

The card's "## 改动预算" section is parsed for lines like
  - 源码新增上限：300 行
  - 源码文件上限：5 个
  - `path/to/file.py`            (expected file; glob patterns allowed; every file a budget line names counts)
  - 测试：`tests/x.py`（新），新增不超过 180 行   (per-file test budget; a "测试…N 行" line without a file caps all tests)
A card that states a test budget is held to it; only cards without one get the default 1.5x test/source ratio.
Files of "## 必须通过的新测试" entries are expected too, and a `::name` entry continues the file of the entry above.
"""
import argparse
import fnmatch
import re
import subprocess
import sys

PATTERNS = [
    ('宽泛捕获', re.compile(r'except\s*(Exception|BaseException)?\s*(as \w+)?\s*:|catch\s*\(')),
    ('吞掉异常', re.compile(r'^\s*(pass|return None|return|continue)\s*(#.*)?$')),
    ('try 块', re.compile(r'^\s*try\s*[:{]')),
    ('类型检查', re.compile(r'\bisinstance\(|\btypeof\b|\bhasattr\(')),
    ('默认值兜底', re.compile(r'\bor (\{\}|\[\]|""|\'\'|0|None)\b|\?\? |\.get\([^)]*,\s*[^)]+\)|getattr\([^)]*,[^)]*,')),
    ('参数校验抛错', re.compile(r'raise \w*(Error|Exception)\(|throw new \w*Error\(')),
    ('重试/回退', re.compile(r'retry|retries|fallback|backoff', re.I)),
    ('可空类型', re.compile(r'Optional\[|\|\s*None\b|\| null\b|\| undefined\b')),
]
TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$')
SKIP_PATH = re.compile(r'(^|/)docs/|\.md$|lock(\.json)?$|\.lock$')
TEST_LINES_FLOOR = 40  # the 1.5x test/source ratio only applies above this many added test lines


def git(wt, *args):
    return subprocess.run(['git', '-C', wt, *args], capture_output=True, text=True, check=True).stdout


FILE_SPAN = re.compile(r'`([^`\s]+)`')
LOOKS_LIKE_FILE = re.compile(r'\.\w+$|\*')
LINES_NUMBER = re.compile(r'(\d+)\s*行')


def matches(path, pattern):
    """A changed repo-relative path matches a card path when it is the same file, matches the glob, or ends with it
    (cards write `test_api.py` or a path relative to a sub-project; git reports paths from the repository root)."""
    return fnmatch.fnmatch(path, pattern) or path.endswith('/' + pattern)


def parse_card(path):
    budget = {'src_lines': None, 'src_files': None, 'expected': [], 'tests': [],
              'test_lines': None, 'test_file_lines': {}, 'test_expected': []}
    if not path:
        return budget
    text = open(path, encoding='utf-8').read()
    m = re.search(r'^##\s*改动预算\s*$(.*?)(?=^##\s|\Z)', text, re.M | re.S)
    if not m:
        return budget
    sec = m.group(1)
    n = re.search(r'源码新增上限[:：]\s*(\d+)', sec)
    f = re.search(r'源码文件上限[:：]\s*(\d+)', sec)
    budget['src_lines'] = int(n.group(1)) if n else None
    budget['src_files'] = int(f.group(1)) if f else None
    for line in sec.splitlines():
        files = [s for s in FILE_SPAN.findall(line) if LOOKS_LIKE_FILE.search(s)]
        # Every file a budget line names is expected, not only a leading one: cards write
        # "- 测试：`tests/x.py`（新），新增不超过 180 行" and "- `a.tsx`、`b.tsx`（只按允许清单改）".
        budget['expected'] += files
        # A test-line budget the card states replaces the default 1.5x ratio (2026-11-15 replay: m2-4, c1, d5a).
        # "测试：`tests/x.py`…不超过 180 行" caps that file; "说明：测试约 100 行…" with no file caps all tests.
        num = LINES_NUMBER.search(line)
        if not num or re.search(r'源码(新增|文件)上限', line):
            continue
        tests_named = [s for s in files if TEST_PATH.search(s)]
        if tests_named:
            budget['test_file_lines'][tests_named[0]] = int(num.group(1))
        elif '测试' in line:
            budget['test_lines'] = int(num.group(1))
    # "## 必须通过的新测试": Claude Code lists `tests/x_test.py::name` entries before dispatch; the file part
    # must show up among the changed test files, otherwise the implementation was not driven by the card.
    t = re.search(r'^##\s*必须通过的新测试\s*$(.*?)(?=^##\s|\Z)', text, re.M | re.S)
    # Entry `tests/x.py::name` → the file must be among changed test files; a bare `name` (the file already
    # existed, or Claude Code wrote only the function) → the name must appear in added lines of a test file.
    # An entry `::name` continues the file of the entry above it (cards list the file once, then `::name` rows).
    current_file = ''
    for entry in re.findall(r'^\s*-\s*`([^`]+)`', t.group(1), re.M) if t else []:
        file_part, sep, name = entry.partition('::')
        if not sep:  # no "::": a path names only the file, anything else is a bare test name
            file_part, name = (entry, '') if ('/' in entry or LOOKS_LIKE_FILE.search(entry)) else ('', entry)
        elif not file_part:
            file_part = current_file
        if file_part:
            current_file = file_part
        budget['tests'].append((file_part, name))
    # A required test's file belongs to the card's change even when the budget list does not repeat it.
    budget['test_expected'] = sorted({f for f, _ in budget['tests'] if f})
    return budget


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--worktree', required=True)
    ap.add_argument('--base', required=True, help='commit the task started from (parent of the task-card commit)')
    ap.add_argument('--card')
    a = ap.parse_args()
    budget = parse_card(a.card)
    # Working tree vs base, plus untracked files Codex created (it cannot commit in linked worktrees).
    # Read-only: never touches the index.
    added = {}
    for line in git(a.worktree, 'diff', '--numstat', a.base).splitlines():
        add, _, path = line.split('\t', 2)
        if add != '-':
            added[path] = int(add)
    untracked = git(a.worktree, 'ls-files', '--others', '--exclude-standard').splitlines()
    for path in untracked:
        try:
            added[path] = sum(1 for _ in open(f'{a.worktree}/{path}', encoding='utf-8', errors='replace'))
        except OSError:
            pass
    src_files, test_files, src_add, test_add = [], [], 0, 0
    for path, add in sorted(added.items()):
        if SKIP_PATH.search(path):
            continue
        if TEST_PATH.search(path):
            test_files.append(path); test_add += add
        else:
            src_files.append(path); src_add += add
    allowed = budget['expected'] + budget['test_expected']
    outside = [p for p in src_files + test_files
               if budget['expected'] and not any(matches(p, g) for g in allowed)]

    hits = []
    tracked_src = [p for p in src_files if p not in untracked]
    diff = git(a.worktree, 'diff', '-U0', a.base, '--', *tracked_src) if tracked_src else ''
    for path in src_files:
        if path in untracked:
            diff += f'\n+++ b/{path}\n@@ -0,0 +1 @@\n' + ''.join(
                '+' + l for l in open(f'{a.worktree}/{path}', encoding='utf-8', errors='replace'))
    cur, lineno = None, 0
    for raw in diff.splitlines():
        if raw.startswith('+++ '):
            cur = raw[6:] if raw.startswith('+++ b/') else None
        elif raw.startswith('@@'):
            lineno = int(re.search(r'\+(\d+)', raw).group(1))
        elif raw.startswith('+') and cur:
            body = raw[1:]
            for name, rx in PATTERNS:
                if rx.search(body):
                    hits.append((name, cur, lineno, body.strip()[:120]))
            lineno += 1

    print('# Diff 审计\n')
    print('| 项 | 实际 | 预算 |\n| --- | --- | --- |')
    print(f"| 源码文件 | {len(src_files)} | {budget['src_files'] or '-'} |")
    print(f"| 源码新增行 | {src_add} | {budget['src_lines'] or '-'} |")
    card_test_budget = budget['test_lines'] is not None or bool(budget['test_file_lines'])
    if card_test_budget:
        caps = [f'共 {budget["test_lines"]} 行'] if budget['test_lines'] is not None else []
        caps += [f'`{f}` {n} 行' for f, n in budget['test_file_lines'].items()]
        print(f'| 测试新增行 | {test_add} | 卡定：{"；".join(caps)} |')
    else:
        print(f'| 测试新增行 | {test_add} | 不超过源码新增行的 1.5 倍（{TEST_LINES_FLOOR} 行以内不计） |')
    over = []
    if budget['src_files'] and len(src_files) > budget['src_files']:
        over.append('源码文件超预算')
    if budget['src_lines'] and src_add > budget['src_lines'] * 1.2:
        over.append('源码新增行超预算 20% 以上')
    if card_test_budget:
        # The card owns the test budget; the 1.5x default only applies to cards that state none.
        if budget['test_lines'] is not None and test_add > budget['test_lines'] * 1.2:
            over.append('测试新增行超出卡定上限 20% 以上')
        for f, cap in budget['test_file_lines'].items():
            got = sum(added[p] for p in test_files if matches(p, f))
            if got > cap * 1.2:
                over.append(f'`{f}` 新增 {got} 行，超出卡定上限 {cap} 行的 20% 以上')
    elif src_add and test_add > max(src_add * 1.5, TEST_LINES_FLOOR):
        over.append(f'测试量远超源码（超过 1.5 倍且超过 {TEST_LINES_FLOOR} 行），检查是否在测不该存在的分支')
    test_added_text = git(a.worktree, 'diff', '-U0', a.base, '--', *[p for p in test_files if p not in untracked]) \
        if any(p not in untracked for p in test_files) else ''
    for path in test_files:
        if path in untracked:
            test_added_text += open(f'{a.worktree}/{path}', encoding='utf-8', errors='replace').read()
    missing_tests = []
    for file_part, name in budget['tests']:
        label = f'{file_part}::{name}' if file_part and name else (file_part or name)
        if file_part:
            if not any(matches(p, file_part) for p in test_files):
                missing_tests.append(label)
        elif not re.search(r'\b' + re.escape(name) + r'\b', test_added_text):
            missing_tests.append(label)
    if missing_tests:
        over.append('卡里“必须通过的新测试”没有出现在改动里（文件未改或新增行里没有该测试名）')
    print('\n## 结论信号\n')
    for o in over or ['预算内']:
        print(f'- {o}')
    if outside:
        print('\n## 预期清单之外的文件\n')
        for p in outside:
            print(f'- `{p}`')
    if missing_tests:
        print('\n## 缺失的必需测试\n')
        for t in missing_tests:
            print(f'- `{t}`')
    print(f'\n## 防御性代码命中（{len(hits)} 处）\n')
    print('每处都要能指向任务卡“编码要求”里列出的边界；指不到的写进下一轮要求删除。\n')
    by = {}
    for h in hits:
        by.setdefault(h[0], []).append(h)
    for name, rows in by.items():
        print(f'### {name}（{len(rows)}）\n')
        for _, f, n, body in rows[:40]:
            print(f'- `{f}:{n}` `{body}`')
        if len(rows) > 40:
            print(f'- …另有 {len(rows) - 40} 处')
        print()
    sys.exit(1 if (over or outside) else 0)


if __name__ == '__main__':
    main()
