# herdr 集成

herdr 桌面版（Herdr.app + herdr-core，CLI `hc`）里五个 agent 的启动方式与角色。派发流程见
`skills/delegate-to-codex/SKILL.md` 的“herdr 通道”。

| 文件 | 作用 | 安装位置 |
| --- | --- | --- |
| `templates/codex-impl.md` | Codex 实现者，gpt-6-astra/xhigh，MCP/插件关 | `~/.config/herdr-core/templates/`（安装时把 `__BIN__` 换成 bin 目录后复制） |
| `templates/opencode-review.md` | opencode + DeepSeek 只读复核 | 同上 |
| `templates/pi-helper.md` | pi + DeepSeek 轻量助手 | 同上 |
| `templates/gemini-helper.md` | Gemini CLI 研究助手：联网查资料、通读长文档或大仓库，不改文件 | 同上 |
| `bin/hc-codex` | 按派发默认值起 Codex；MCP 全关，只留 Codex 自带的电脑控制（可操作桌面应用和 Chrome） | `~/.local/bin/hc-codex`（链接） |
| `bin/hc-opencode-deepseek` | 从钥匙串取 key 起 opencode，禁止编辑，只读命令免批准；带 Chrome 工具（Claude Code 的浏览器桥） | `~/.local/bin/` |
| `bin/hc-pi` | 从钥匙串取 key 起 pi，忽略项目内 .pi 文件；带 Chrome 工具（安装时登记到 `~/.pi/agent/mcp.json`） | `~/.local/bin/` |
| `bin/hc-gemini` | 起 Gemini CLI：钥匙串有 `gemini-api-key` 就用它，否则用 gemini 自己的 Google 账号登录；套 `policies/gemini-readonly.toml`（禁止改文件，`hc send` 免批准，其他命令仍需母会话确认）；带 Chrome 工具（安装时登记到 `~/.gemini/settings.json`）；`HC_GEMINI_MODEL` 换模型 | `~/.local/bin/` |
| `bin/hc-archive` | 归档一个会话：`hc-archive <会话终端> --plan` 只列出会做什么，不带 `--plan` 执行。结束它下面所有层级的子 agent，归还它们和会话自己的任务工作树（干净的走 `task_worktree.py return`，有改动的先由 `workspace_hygiene.py` 快照进废纸篓），关掉子 agent 分支上打开的 PR（留评论，远端分支保留，可重开）；会话自己的 PR 不动。侧栏会话行的归档按钮调用它，确认后再关标签页 | `~/.local/bin/hc-archive`（链接） |
| `bin/hc-usage` | 侧栏“用量”一栏的数据：各 agent 的剩余额度与今日用量（JSON）。Codex 读今天的 rollout 记录；DeepSeek（opencode / pi）用钥匙串里的 key 查余额，今日花费取 pi 会话记录与 `opencode stats`；Claude Code 的今日 tokens 取转录，额度取 `~/.herdr/usage/claude.json` | `~/.local/bin/hc-usage`（链接） |
| `bin/hc-schedules` | 侧栏 “Scheduled” 标签页的数据（JSON，只读）：本机会自己按时跑的任务，含状态（在跑 / 暂停）、下次与上次运行时间、上次没跑或退出码非零的提示。来源是活着的 Claude Code 会话在会话内排的任务（CronCreate / `/loop`，从转录读；循环任务 7 天到期，同一会话开在两个进程里会提示可能重复跑）、`hc-task` 的任务（行下显示上次时间、状态和结果首行，悬停看结果前 400 字，点击打开它的会话；它们的 launchd 任务不再重复列）、Claude 桌面版当前账户的 `scheduled-tasks.json`、`~/.codex/automations/`、`~/Library/LaunchAgents/` 里带时间表的任务（常驻服务和应用自带的更新器不算）。跑过的一次性任务不列；云端 routine 本机读不到，不在其中。侧栏按来源分组（Scheduled (herdr) / In sessions / Claude Desktop / Codex automations / System (launchd)），组可折叠，herdr 组标题右边 “+” 新建任务、“All results” 打开 Results 页签，herdr 组的行悬停出现 Run now / Edit，跑的时候行下显示 Running now。其他来源的启停仍在各自的工具里做 | `~/.local/bin/hc-schedules`（链接） |
| `mods/sched-task/` | 定时任务编辑弹窗（侧栏 “+” 或行上的 Edit 打开）：名称、提示词、cron（带预设和下几次时间预览）、目录（可选文件夹）、权限模式、模型、Chrome、开 / 暂停；按钮 Save、Save & run now（先存再后台跑一轮）、Delete（框内确认）。全部经 `hc-task show/save/next/run --detach/rm` | `~/.config/herdr-app/mods/sched-task/`（复制） |
| `bin/hc-task` | herdr 的定时任务（2026-11-21 起替代 Claude 桌面版定时任务）：launchd 按 cron 触发 `hc-task run <id>`，把提示词发进该任务的常驻终端 `sched-<id>`，里面是固定会话 id 的交互式 `claude`（不在跑就续上同一会话再起），在 herdr 空间 “Scheduled”里一个任务一个页签，“Results”页签滚动显示各任务最新结果；等这一轮结束后把转录里最后一条助手文字记进 `runs/`。任务在 `~/.agents/scheduled-tasks/<id>/`（`task.json`、`prompt.md`、`runs/` 只留最近 60 次），所有任务的最新结果在同目录 `RESULTS.md`。`ls` / `results` / `open [<id>]`（不带 id 打开 Results 页签）/ `show <id>` / `save [--json …]`（新建或修改，校验名称、提示词、cron、目录、权限模式；改了目录换新会话，改了启动参数且终端空闲就关掉它，下一轮用新参数续上）/ `next '<cron>'`（预览下几次时间）/ `run <id> [--detach]` / `on` / `off` / `sync` / `import-desktop [--apply]` / `rm`（关掉会话终端、目录移进废纸篓）；暂停的任务导入后仍是暂停，启用由用户决定 | `~/.local/bin/hc-task`（链接）；launchd 任务 `~/Library/LaunchAgents/local.hc-task.<id>.plist` 由 `on`/`off`/`sync` 写和删 |
| `bin/hc-cc-statusline` | Claude Code 状态栏脚本：把输入里的 `rate_limits` 写到 `~/.herdr/usage/claude.json`（单个文件，每次覆盖，不累积）并显示 `5h x% · 周 y%`。需要在 `~/.claude/settings.json` 里设 `"statusLine": {"type": "command", "command": "~/.local/bin/hc-cc-statusline"}`，安装脚本不改这个文件；没设时侧栏显示“额度未接入” | `~/.local/bin/hc-cc-statusline`（链接） |
| `bin/hc-dispatch` | 会话内派发：`hc-dispatch <模板> --goal … [--facts …] [--file …] [--done …] <任务>`，在任务前附“母会话上下文”（目标、已定事实、完成标准、母终端、工作树/分支/HEAD、未提交改动、母会话转录路径）；`--goal`、`--name` 必填，`--print` 只看不发；`--to <名字>` 把关联任务续派给同一个子 agent；`--worktree` 给要改文件的子 agent 单开工作树（`.worktrees/<名字>`，分支 `task/<名字>`，从母会话 HEAD 分出）；回合结束时自动通知母终端；子 agent 不清理，随母会话结束。带卡的 Codex 派发仍用 `herdr_dispatch.py` | `~/.local/bin/hc-dispatch`（链接） |
| `layouts/projects.json` | 个人布局：`main` 的克隆，`spaces` 块换成 `projects` | `~/.config/herdr-app/layouts/`（缺失时复制） |
| `install.sh` | `install` 建链接并复制 mod/布局，`check` 报告缺失或不一致；从主检出运行 | 不安装 |
| `mods/projects/` | 项目侧栏：项目 → 会话（中控 agent，默认 Claude Code）→ 子 agent，右侧圆点是状态；最下面一栏分“用量”和“定时任务”两个标签页；一个项目可绑定多个文件夹，`main` 指定主项目（缺省为第一个）；项目清单在其中的 `projects.json` | `~/.config/herdr-app/mods/projects/`（复制；`projects.json` 只在缺失时落种子） |
| `../scripts/herdr_cc_status.py` | Claude Code 钩子，向 herdr 上报 OSC 7501 状态 | `~/.claude/settings.json` 各生命周期事件 |

## 项目、记忆与约定

- **项目**：每个在办仓库一个 herdr 空间，folder 等于仓库根（`~/.local/bin/herdr-app ls` 可查），首个页签叫“主控”。
  项目会话在主控页签里运行 `claude`；派发起的 Codex、复核、助手终端带 `herdr.space` 标签，自动出现在同一空间。
  新仓库先在 herdr 窗口里建空间再派发，否则 `herdr_dispatch.py` 起的终端不进窗口。
- **记忆**：不搬。Claude Code 记忆按工作目录存在 `~/.claude/projects/`，Codex 的在 `~/.codex/`，在 herdr 终端里同一路径启动就沿用。
- **约定**：全局约定只写一份（示例见 `agent-context/global.example.md`），链到 `~/AGENTS.md`、`~/.claude/CLAUDE.md`、
  `~/.gemini/AGENTS.md`、`~/.config/opencode/AGENTS.md`；Codex 读 `~/.codex/AGENTS.md`，pi 沿目录向上读到 `~/AGENTS.md`。
  仓库级 `AGENTS.md` 各 agent 都读。

DeepSeek key 只放钥匙串，服务名 `deepseek-api`，自己录入：

```bash
security add-generic-password -s deepseek-api -a "$USER" -w
```

Gemini 二选一：钥匙串录入 `gemini-api-key`（同上命令换服务名），或在任一终端运行 `gemini` 用 Google 账号登录。
侧栏“用量”暂不含 Gemini。

安装与检查：在主检出里运行 `herdr/install.sh install`，之后 `herdr/install.sh check`。

撤销：删除上面的链接、`~/.config/herdr-app/mods/projects/` 与 `layouts/projects.json`（窗口用 `cmd-shift-l` 切回 `main` 布局）、`~/.claude/settings.json` 里指向 `herdr_cc_status.py` 的钩子即可，不影响 Codex App 通道。
