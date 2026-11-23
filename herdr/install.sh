#!/usr/bin/env bash
# Install or check the herdr pieces of this harness on the local machine.
#   herdr/install.sh install   link bin/hc-*, render templates, copy the sidebar mods and layout
#   herdr/install.sh check     report anything missing or different; exit 1 if so
# Links point at this checkout, so run it from the main checkout, not a task worktree.
# The mod is copied (Herdr hot-reloads real files); projects.json (the project list, edited from the sidebar)
# and the layout are only seeded when absent, never overwritten.
# HERDR_BIN_DIR / HERDR_CORE_HOME / HERDR_APP_HOME override the targets (used to verify in an isolated directory).
set -euo pipefail
action="${1:-}"
[[ "$action" == install || "$action" == check ]] || { echo "usage: $0 <install|check>" >&2; exit 2; }
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
bin="${HERDR_BIN_DIR:-$HOME/.local/bin}"
core="${HERDR_CORE_HOME:-$HOME/.config/herdr-core}"
app="${HERDR_APP_HOME:-$HOME/.config/herdr-app}"
bad=0
say() { echo "$1"; }
fail() { echo "FAIL $1" >&2; bad=1; }

link() { # link <target> <link path>
  if [[ "$(readlink "$2" 2>/dev/null || true)" == "$1" ]]; then return; fi
  if [[ "$action" == check ]]; then fail "$2 不是指向 $1 的链接"; return; fi
  if [[ -e "$2" && ! -L "$2" ]]; then fail "$2 已存在且不是链接，未覆盖"; return; fi
  mkdir -p "$(dirname "$2")"; ln -sfn "$1" "$2"; say "linked $2"
}
copy() { # copy <source> <dest> <always|seed>
  if [[ -f "$2" ]] && cmp -s "$1" "$2"; then return; fi
  if [[ "$3" == seed && -f "$2" ]]; then return; fi
  if [[ "$action" == check ]]; then fail "$2 与仓库的 ${1#$src/} 不一致或缺失"; return; fi
  mkdir -p "$(dirname "$2")"; cp "$1" "$2"; say "copied $2"
}

for f in "$src"/bin/hc-*; do link "$f" "$bin/$(basename "$f")"; done
# Templates name their launcher by absolute path; render __BIN__ to the bin directory chosen above.
render() { # render <template> <dest>
  if [[ -f "$2" ]] && sed "s#__BIN__#$bin#g" "$1" | cmp -s - "$2"; then return; fi
  if [[ "$action" == check ]]; then fail "$2 与仓库的 ${1#$src/} 不一致或缺失"; return; fi
  if [[ -L "$core/templates" ]]; then fail "$core/templates 是链接，先删掉它再安装"; return; fi
  mkdir -p "$(dirname "$2")"; sed "s#__BIN__#$bin#g" "$1" > "$2"; say "rendered $2"
}
for f in "$src"/templates/*.md; do render "$f" "$core/templates/$(basename "$f")"; done
# pi reads MCP servers only from its own file: register Claude Code's Chrome bridge there once.
if command -v pi >/dev/null && [[ -x "$HOME/.local/bin/claude" ]] && ! grep -qs claude-in-chrome-mcp "$HOME/.pi/agent/mcp.json"; then
  if [[ "$action" == check ]]; then fail "pi 没有登记 chrome（~/.pi/agent/mcp.json）"
  else pi mcp add chrome -- "$HOME/.local/bin/claude" --claude-in-chrome-mcp >/dev/null && say "pi: registered chrome"; fi
fi
# Gemini keeps MCP servers in ~/.gemini/settings.json: register the same Chrome bridge there once (user scope).
if command -v gemini >/dev/null && [[ -x "$HOME/.local/bin/claude" ]] && ! grep -qs claude-in-chrome-mcp "$HOME/.gemini/settings.json"; then
  if [[ "$action" == check ]]; then fail "gemini 没有登记 chrome（~/.gemini/settings.json）"
  else gemini mcp add -s user chrome "$HOME/.local/bin/claude" --claude-in-chrome-mcp >/dev/null && say "gemini: registered chrome"; fi
fi
copy "$src/mods/projects/main.js" "$app/mods/projects/main.js" always
copy "$src/mods/projects/mod.json" "$app/mods/projects/mod.json" always
copy "$src/mods/projects/projects.json" "$app/mods/projects/projects.json" seed
copy "$src/mods/sched-task/main.js" "$app/mods/sched-task/main.js" always
copy "$src/mods/sched-task/mod.json" "$app/mods/sched-task/mod.json" always
copy "$src/mods/new-session/main.js" "$app/mods/new-session/main.js" always
copy "$src/mods/new-session/mod.json" "$app/mods/new-session/mod.json" always
copy "$src/layouts/projects.json" "$app/layouts/projects.json" seed
# Claude Code 的额度靠状态栏脚本落盘；settings.json 归 用户，这里只提示，不改也不算失败。
settings="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
grep -q 'hc-cc-statusline' "$settings" 2>/dev/null \
  || say "NOTE $settings 的 statusLine 没有指向 hc-cc-statusline：侧栏用量里 Claude Code 的额度不会更新（见 herdr/README.md）"
[[ $bad == 0 ]] && say "herdr $action ok" || exit 1
