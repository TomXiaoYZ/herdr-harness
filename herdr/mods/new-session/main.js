// 新会话选哪个 agent 当中控（项目行的 “+” 打开）。props { folder, space? }：有空间就在里面开标签页，没有就新建空间。
// 每个 agent 用 harness 的启动方式：OpenCode 以 --lead 起（可改文件），Pi 与 AGY 用钥匙串 / ADC 登录的包装脚本。
// ↑/↓ 选，Enter 或 1–5 开，Esc 关。图标取 herdr 自己的 agent 列表。

import { View, div, svg, image } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme, knownAgents, run, closeOverlay, props, home } from "herdr";

const BIN = () => `${home()}/.local/bin`;
const CHOICES = () => [
  { key: "claude", name: "Claude Code", argv: ["claude"] },
  { key: "codex", name: "Codex", argv: ["codex", "--no-daemon"] },
  { key: "opencode", name: "OpenCode", argv: [`${BIN()}/hc-opencode-deepseek`, "--lead"], hint: "DeepSeek" },
  { key: "pi", name: "Pi", argv: [`${BIN()}/hc-pi`], hint: "DeepSeek" },
  { key: "antigravity", name: "AGY", argv: [`${BIN()}/hc-agy`], hint: "Gemini 4 Neon" },
];
const base = (p) => p.replace(/\/+$/, "").split("/").pop();

export default class NewSession extends View {
  init(_props, cx) {
    this.icons = {};
    this.at = 0;
    this.handle = cx.focus_handle();
    this.handle.focus();
    cx.spawn(async (cx) => {
      try { for (const a of await knownAgents({})) this.icons[a.key] = a; } catch { /* 没图标也能开 */ }
      cx.notify();
    });
  }

  open(c) {
    const p = props() ?? {};
    closeOverlay(p.overlay);
    if (p.space) run("tab.new", { space: p.space, folder: p.folder, name: c.name, argv: c.argv });
    else run("space.new", { name: base(p.folder), folder: p.folder, argv: c.argv });
  }

  render() {
    const t = theme(), list = CHOICES();
    const pic = (c) => { const a = this.icons[c.key];
      return !a ? div().size(14).flex_none() : a.tint ? svg(a.icon).size(14).flex_none().text_color(t.foreground) : image(a.icon).size(14).flex_none(); };
    return v_flex().size_full().bg(t.popover).p(5).text_size(12.8).id("new-session").track_focus(this.handle)
      .on_key_down((e, cx) => {
        if (e.modifiers.control || e.modifiers.platform || e.modifiers.alt) return;
        if (e.key === "escape") closeOverlay(props()?.overlay);
        else if (e.key === "down" || e.key === "up") { this.at = (this.at + (e.key === "down" ? 1 : list.length - 1)) % list.length; cx.notify(); }
        else if (e.key === "enter") this.open(list[this.at]);
        else if (/^[1-9]$/.test(e.key) && list[Number(e.key) - 1]) this.open(list[Number(e.key) - 1]);
      })
      .child(div().px(9).pt(4).pb(4).text_size(11).text_color(t.dim).child("New session with"))
      .children(list.map((c, i) => h_flex().id(`new-session-${c.key}`).h(31).px(9).gap(10).rounded(7).items_center().flex_none().cursor_pointer()
        .text_color(t.foreground).when(i === this.at, (el) => el.bg(t.selected)).when(i !== this.at, (el) => el.hover((s) => s.bg(t.hover)))
        .on_click(() => this.open(c))
        .child(pic(c)).child(div().flex_1().child(c.name))
        .child(div().text_size(11).text_color(t.dim).child(c.hint ?? String(i + 1)))));
  }
}
