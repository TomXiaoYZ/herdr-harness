# herdr-harness

一套让多个 coding agent（Claude Code、Codex、opencode、pi、Gemini CLI、Antigravity CLI）在同一个桌面窗口里分工协作的个人 harness：
母会话（默认 Claude Code，新建时可选其他 agent）负责规划、拆卡和验收，把实现、复核、查资料派给子 agent；
每个子 agent 在自己的终端和工作树里干活，母会话在侧栏里一眼看到谁在跑、谁在等你、谁做完了。

建在 [herdr](https://github.com/herdrdev/herdr)（Herdr.app + `hc` CLI，Apache-2.0）之上。本仓库不包含 herdr 本身，
只包含外围：侧栏 mod、`hc-*` 包装脚本、agent 模板、派发技能和约定。

> English summary: a personal multi-agent harness on top of herdr. The parent session (Claude Code by default, or
> another agent picked when the session opens) plans, writes task cards and accepts; Codex implements,
> opencode/pi/Gemini/Antigravity review and research; all agents share one per-project memory. Includes a sidebar mod
> (projects → sessions → sub-agents with live status, PR/CI, usage and scheduled tasks), `hc-dispatch` for
> in-session delegation with automatic wake-up of the parent, a card-based Codex delegation skill with
> external acceptance gates, and borrowed-environment hygiene (worktrees are returned when the task closes).
> Docs and prompts are in Chinese.

## 设计要点

- **母会话只做决定，不做体力活。** 规划、拆卡、验收在母会话；实现在子 agent 的独立工作树里。
  卡是不含源码、决策完整的交接包（≤12 KB），最多两轮；卡的“授权”节是唯一派发参数来源，放宽就开新卡。
- **门控看外部证据，不看自评。** Codex 回合末的 Stop 钩子跑 `diff_audit.py`（改动预算、防御性代码、越界文件），
  验收先从卡写清单再读 diff；回执只是声明。
- **派发带上下文，回合结束会叫醒母会话。** `hc-dispatch` 在任务前附母会话的目标、已定事实、完成标准、工作树和未提交改动；
  子 agent 的回合真正结束（不是“刚派出去还没开始”）时才通知母终端，母会话不会空等，也不会漏收。
- **侧栏是项目 → 会话 → 子 agent 的树。** 每行右侧圆点是状态（运行中 / 等你 / 完成 / 空闲），下面是工作树、PR 和 CI；
  底部两个页签是各 agent 的剩余额度与今日用量、本机所有定时任务。会话行的归档按钮一次收掉下面所有层的子 agent、
  归还工作树、关掉子 agent 的 PR。项目行的 “+” 弹出中控 agent 选择（Claude Code / Codex / OpenCode / Pi / AGY）。
- **一个项目一份记忆。** 就是 Claude Code 按仓库记的那份；其他 agent 开工跑 `hc-memory` 读，照同一格式写回。
- **环境是借的。** 工作树只用 `task_worktree.py new` 建（每仓库上限 8，可稀疏检出），创建时就登记归还条件；
  `workspace_hygiene.py` 按 PR 状态对账，有改动的先快照进废纸篓再删。
- **凭据只在钥匙串。** DeepSeek / Gemini key 运行时从 macOS 钥匙串读出、只导出给对应进程，不落文件；AGY 走 Google Cloud ADC。

## 目录

| 路径 | 内容 |
| --- | --- |
| `herdr/` | 侧栏 mod（`mods/projects`、`mods/sched-task`、`mods/new-session`）、`bin/hc-*` 包装脚本、agent 模板、Gemini 只读策略、布局、`install.sh`；逐个说明见 [herdr/README.md](herdr/README.md) |
| `skills/delegate-to-codex/` | 卡片式派发 Codex 的技能：任务卡与验收简报模板、`herdr_dispatch.py`、`task_worktree.py`、`diff_audit.py`、`check_card.py`、Claude Code 编辑守卫等 |
| `skills/frontend-craft/` | Web UI 的实现与验收底线（token、数值底线、界面审计、去 AI 味扫描、渲染截图证据），上游来源与许可见 `references/sources.md` |
| `scripts/herdr_cc_status.py` | Claude Code 钩子，向 herdr 上报运行 / 等待 / 完成状态 |
| `scripts/workspace_hygiene.py` | 工作树与 Docker 残留对账 |
| `agent-context/global.example.md` | 全机协作约定示例 |
| `tests/` | 派发、定时任务、归档、用量相关测试 |

## 依赖

- macOS（launchd、钥匙串；脚本 shebang 用 Homebrew 的 `/opt/homebrew/bin/python3`）
- [herdr](https://github.com/herdrdev/herdr) 桌面版与 `hc`，装在 `~/.local/bin`
- 按需：Claude Code、Codex CLI、opencode、pi、Gemini CLI、Antigravity CLI（`agy`，配 `gcloud`）、`gh`

## 安装

```bash
git clone <this repo> ~/Code/herdr-harness && cd ~/Code/herdr-harness
herdr/install.sh install      # 链接 bin/hc-*、渲染模板、复制侧栏 mod 与布局
herdr/install.sh check
```

然后：

- 把 `skills/*` 链接到各 agent 的技能目录（例如 `~/.claude/skills/`）。
- 在 `~/.claude/settings.json` 里把各生命周期事件的钩子指向 `scripts/herdr_cc_status.py`，`statusLine` 指向 `~/.local/bin/hc-cc-statusline`（侧栏用量要用）。
- 编辑侧栏项目清单 `~/.config/herdr-app/mods/projects/projects.json`（也可在侧栏里加）。
- 可选环境变量：`CODEX_NEVER_MCP`（任何授权都不打开的 MCP 服务器，逗号分隔）、`CC_EDIT_FULL_ACCESS`
  （Claude Code 可直接改代码的仓库，冒号分隔；默认只有本仓库）、`HC_AGY_PROJECT`（侧栏用量查 AGY 的 GCP 项目，不设就不显示）
  与 `HC_AGY_DAY_TOKENS` / `HC_AGY_MONTH_TOKENS`（日 / 月 token 上限）。

测试：`python3 -m pytest tests`。

## 许可

MIT，见 [LICENSE](LICENSE)。`skills/frontend-craft` 中改编的第三方内容保留各自许可（MIT / Apache-2.0），见其 `references/sources.md`。
