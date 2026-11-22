#!/usr/bin/env python3
"""Pre-dispatch check of a task card. Exit 1 with the reasons when the card should not go out yet.

  check_card.py <task card README.md> [--round N]

Checks:
- size: at most 12 KiB (the card is decision-complete, not source-complete; Codex reads the worktree itself)
- no pasted source: fenced code blocks longer than 8 lines, except the receipt-format block
- required sections present, budget numbers present, expected files listed
- "必须通过的新测试" lists at least one test, or says 无 with a reason
- rounds: a third round is not dispatched; decompose or escalate instead
- persistent writes (volumes, /var/lib, cache/checkpoint dirs) come with a retention policy and a cleanup entry point
"""
import argparse
import os
import re
import sys

MAX_BYTES = 12 * 1024
MAX_FENCE_LINES = 8
MAX_ROUNDS = 2
REQUIRED = ['目标', '授权', '输入清单', '人工前置条件', '用户路径', '验收标准', '改动预算', '必须通过的新测试',
            '编码要求', '边界', '测试入口', '完成定义', '回执格式', '轮次']


def section(text, title):
    m = re.search(rf'^##\s*{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)', text, re.M | re.S)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('card')
    ap.add_argument('--round', type=int, default=None, help='round about to be dispatched (default: count "### 第 N 轮" headings)')
    ap.add_argument('--kind', choices=('dev', 'handoff'), default='dev', help='handoff cards (assets/handoff-card.md) skip the budget/test checks')
    a = ap.parse_args()
    text = open(a.card, encoding='utf-8').read()
    problems = []

    size = os.path.getsize(a.card)
    if size > MAX_BYTES:
        problems.append(f'卡 {size} 字节，超过 {MAX_BYTES}：去掉贴进来的源码和日志，改成路径与符号引用，或拆卡')

    fences = re.findall(r'^```[^\n]*\n(.*?)^```', text, re.M | re.S)
    receipt = section(text, '回执格式') or ''
    for body in fences:
        n = body.count('\n')
        if n > MAX_FENCE_LINES and body not in receipt:
            problems.append(f'有 {n} 行的代码块：卡里不贴源码，写文件路径和符号名')

    required = REQUIRED if a.kind == 'dev' else ['目标', '产出', '验收标准', '边界', '回执格式']
    for title in required:
        if section(text, title) is None:
            problems.append(f'缺小节 “## {title}”')

    budget = section(text, '改动预算') or ''
    if a.kind == 'handoff':
        budget = '源码新增上限：0 源码文件上限：0\n- `docs/handoff/*.md`'
    if not re.search(r'源码新增上限[:：]\s*\d+', budget) or not re.search(r'源码文件上限[:：]\s*\d+', budget):
        problems.append('改动预算缺数字（源码新增上限 / 源码文件上限）')
    lines = re.search(r'源码新增上限[:：]\s*(\d+)', budget)
    if lines and int(lines.group(1)) > 300 and '理由' not in budget:
        problems.append('源码新增上限超过默认 300 行，卡里没写拆不动的理由')
    if not re.findall(r'^\s*-\s*`[^`]+`', budget, re.M):
        problems.append('改动预算没有列出预期文件')

    tests = section(text, '必须通过的新测试') or ('无：交接文档' if a.kind == 'handoff' else '')
    test_entries = re.findall(r'^\s*-\s*`([^`]+)`', tests, re.M)
    if not test_entries and '无' not in tests:
        problems.append('“必须通过的新测试”为空：由 Claude Code 先列出测试名和断言，或写“无”并给理由')
    bad_tests = [t for t in test_entries if not re.match(r'^[^:\s`]+\.\w+::\S+$', t)]
    if bad_tests:
        problems.append('“必须通过的新测试”条目必须写成 `tests/路径.py::测试名`（文件已存在也要写路径），不合格：'
                        + '、'.join(f'`{t}`' for t in bad_tests[:5]))

    mandate = section(text, '授权') or ''
    if a.kind == 'dev':
        for k in ('网络', '额外可写目录', 'MCP', '模型', '强度'):
            if not re.search(rf'^\s*-\s*{k}[:：]', mandate, re.M):
                problems.append(f'“## 授权”缺 “{k}” 一行（派发参数只从这里读）')
        if re.search(r'^\s*-\s*MCP[:：]\s*开', mandate, re.M) and '理由' not in mandate:
            problems.append('授权打开了 MCP 但没写理由')
        if re.search(r'^\s*-\s*(模型|强度)[:：]\s*(?!默认)', mandate, re.M) and '理由' not in mandate:
            problems.append('授权改了模型或强度但没写理由')
    if '- human_action:' not in (section(text, '回执格式') or '') and a.kind == 'dev' and 'human_action' not in text:
        problems.append('回执格式缺 human_action 字段（结构化提问）')

    body_no_fence = re.sub(r'```.*?```', '', text, flags=re.S)
    trigger_text = re.sub(r'^.*保留策略.*$', '', body_no_fence, flags=re.M)  # the policy line itself names the triggers
    if a.kind == 'dev' and re.search(r'volumes?\s*:|docker volume|/var/lib/|持久化|检查点目录|缓存目录', trigger_text) \
            and not re.search(r'保留策略[:：]\s*(?!无\b)\S|retention[:：]\s*\S', body_no_fence):
        problems.append('卡涉及新增持久化写入（卷、/var/lib、缓存/检查点目录）但没写保留策略与清理入口（2026-11-10：某仓库检查点 12 天 34 GB 的教训）')

    if re.search(r'<[^>\n]{1,20}>', body_no_fence):
        problems.append('还有尖括号占位符没填')

    rounds = a.round if a.round is not None else len(re.findall(r'^###\s*第\s*\d+\s*轮', text, re.M))
    if rounds > MAX_ROUNDS:
        problems.append(f'这是第 {rounds} 轮：不派第 {MAX_ROUNDS + 1} 轮，拆卡或带证据交用户')

    if problems:
        print('任务卡未通过派出前自检：')
        for p in problems:
            print(f'- {p}')
        return 1
    print(f'任务卡通过自检：{size} 字节，第 {rounds or 1} 轮')
    return 0


if __name__ == '__main__':
    sys.exit(main())
