// Scheduled task editor: add or change an hc-task (name, prompt, schedule,
// folder, permission mode, model, Chrome, on/off), run it now, or delete it.
// Opened as a dialog from the sidebar's Scheduled (herdr) group:
// openOverlay({ mod: "sched-task", kind: "dialog", props: { id? } }).
// Everything goes through ~/.local/bin/hc-task (show / save / next / run --detach / rm).

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { exec, home, theme, props, closeOverlay, pickFolder, notify } from "herdr";

const I = (name) => `mods/workspace-chrome/${name}.svg`;
const BIN = () => `${home()}/.local/bin/hc-task`;
const tilde = (p) => (p && p.startsWith(home()) ? "~" + p.slice(home().length) : p ?? "");

const PRESETS = [
  ["Hourly", "0 * * * *"],
  ["Daily 9:00", "0 9 * * *"],
  ["Weekdays 9:00", "0 9 * * 1-5"],
  ["Mondays 9:00", "0 9 * * 1"],
];
const MODES = [
  ["auto", "Auto", "Classifier approves safe actions, asks otherwise"],
  ["acceptEdits", "Accept edits", "File edits go through, commands ask"],
  ["manual", "Ask", "Every action waits for you in the session"],
  ["bypassPermissions", "Bypass", "Nothing asks: only for trusted prompts"],
];
const MODELS = [["", "Default"], ["fable", "Fable"], ["opus", "Opus"], ["sonnet", "Sonnet"], ["haiku", "Haiku"]];

const fmtNext = (iso) => {
  const d = new Date(iso);
  const p = (x) => String(x).padStart(2, "0");
  const day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()];
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
  return `${day} ${mon} ${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

export default class SchedTask extends View {
  init(_props, cx) {
    this.id = props()?.id ?? null;
    this.loading = !!this.id;
    this.focus = null;
    this.mode = "auto";
    this.model = "";
    this.chrome = false;
    this.enabled = true;
    this.cwd = home();
    this.busy = null; // "save" | "run" | "delete"
    this.error = null;
    this.flash = null;
    this.confirmDelete = false;
    this.running = false;
    this.next = null; // { next: [...] } | { error }
    this.handle = cx.focus_handle();

    const field = (key, placeholder) => {
      const s = InputState.new({ placeholder });
      s.on("focus", (_e, cx) => { this.focus = key; cx.notify(); });
      s.on("blur", (_e, cx) => { if (this.focus === key) this.focus = null; cx.notify(); });
      s.on("change", (_e, cx) => { this.error = null; this.flash = null; cx.notify(); });
      return s;
    };
    this.name = field("name", "Task name");
    this.cron = field("cron", "0 9 * * 1-5");
    this.cron.on("change", (_e, cx) => this.preview(cx));
    this.folder = field("folder", "~/Code/project");
    this.prompt = TextareaState.new({ placeholder: "What Claude does on each run" });
    this.prompt.set_auto_grow(6, 14);
    this.prompt.on("focus", (_e, cx) => { this.focus = "prompt"; cx.notify(); });
    this.prompt.on("blur", (_e, cx) => { if (this.focus === "prompt") this.focus = null; cx.notify(); });
    this.prompt.on("change", (_e, cx) => { this.error = null; this.flash = null; cx.notify(); });

    if (this.id) {
      cx.spawn(async (cx) => {
        const r = await exec(BIN(), ["show", this.id], { timeout: 15000 }).catch((e) => ({ code: 1, stderr: String(e) }));
        try {
          const t = JSON.parse(r.stdout);
          this.name.set_value(t.name ?? "");
          this.cron.set_value(t.cron ?? "");
          this.folder.set_value(tilde(t.cwd));
          this.prompt.set_value((t.prompt ?? "").replace(/\n$/, ""));
          this.mode = t.permission_mode || "auto";
          this.model = t.model || "";
          this.chrome = !!t.chrome;
          this.enabled = !!t.enabled;
          this.running = !!t.running;
        } catch {
          this.error = (r.stderr || r.stdout || "could not read the task").trim();
        }
        this.loading = false;
        this.preview(cx);
        cx.notify();
      });
    } else {
      this.cron.set_value("0 9 * * 1-5");
      this.folder.set_value(tilde(home()));
      this.preview(cx);
      this.name.focus();
      cx.timer.after(300, () => this.name.focus());
    }
    // While a run is going, watch for it to end.
    cx.timer.every(3000, (cx) => {
      if (!this.id || !this.running) return;
      cx.spawn(async (cx) => {
        const r = await exec(BIN(), ["show", this.id], { timeout: 15000 }).catch(() => null);
        try {
          const t = JSON.parse(r.stdout);
          if (this.running && !t.running) this.flash = "Run finished: see the sidebar or the Results tab.";
          this.running = !!t.running;
          cx.notify();
        } catch {}
      });
    });
  }

  preview(cx) {
    const cron = this.cron.value().trim();
    const seq = (this.seq = (this.seq ?? 0) + 1);
    cx.spawn(async (cx) => {
      const r = await exec(BIN(), ["next", cron], { timeout: 10000 }).catch(() => null);
      if (seq !== this.seq) return;
      try { this.next = JSON.parse(r.stdout); } catch { this.next = null; }
      cx.notify();
    });
  }

  close() {
    closeOverlay(props()?.overlay);
  }

  data() {
    const folder = this.folder.value().trim();
    return {
      ...(this.id ? { id: this.id } : {}),
      name: this.name.value(),
      cron: this.cron.value(),
      cwd: folder.startsWith("~") ? home() + folder.slice(1) : folder,
      prompt: this.prompt.value(),
      permission_mode: this.mode,
      model: this.model,
      chrome: this.chrome,
      enabled: this.enabled,
    };
  }

  async save() {
    const r = await exec(BIN(), ["save", "--json", JSON.stringify(this.data())], { timeout: 60000 }).catch((e) => ({ code: 1, stderr: String(e) }));
    if (r.code !== 0) throw new Error((r.stderr || r.stdout || "save failed").trim().replace(/^hc-task: /, ""));
    const out = JSON.parse(r.stdout);
    this.id = out.id;
    return out;
  }

  act(kind, cx) {
    if (this.busy || this.loading) return;
    this.busy = kind;
    this.error = null;
    this.flash = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        if (kind === "save") {
          const out = await this.save();
          notify({ title: `Saved ${this.name.value().trim()}`, body: out.note ?? (this.enabled ? `Next: ${this.next?.next?.[0] ? fmtNext(this.next.next[0]) : this.cron.value()}` : "Paused"), level: "success", timeout: 4000 });
          this.close();
          return;
        }
        if (kind === "run") {
          await this.save(); // runs what the form shows
          const r = await exec(BIN(), ["run", this.id, "--detach"], { timeout: 20000 });
          if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^hc-task: /, ""));
          this.running = true;
          this.flash = "Running in its session now. You can close this; the result lands in the sidebar.";
        }
        if (kind === "delete") {
          const r = await exec(BIN(), ["rm", this.id], { timeout: 30000 });
          if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^hc-task: /, ""));
          notify({ title: `Deleted ${this.name.value().trim()}`, body: "Its folder is in the Trash.", level: "info", timeout: 4000 });
          this.close();
          return;
        }
      } catch (e) {
        this.error = String(e.message ?? e);
      }
      this.busy = null;
      cx.notify();
    });
  }

  key(e, cx) {
    if (e.key === "escape") { this.close(); cx.stop_propagation(); }
  }

  label(t, text, hint) {
    return h_flex().pt(14).pb(6).gap(8).items_baseline()
      .child(div().text_size(12).font_medium().text_color(t.foreground).child(text))
      .when(!!hint, (el) => el.child(div().text_size(11.5).text_color(t.dim).child(hint)));
  }

  input(t, state, key) {
    return Input.new(state).h(32).px(10).items_center().rounded(7).border_1()
      .border_color(this.focus === key ? t.accent : t.input).bg(t.background).text_size(13).text_color(t.foreground);
  }

  chip(t, id, text, on, click) {
    return div().id(id).h(26).px(10).flex().items_center().rounded(6).border_1()
      .border_color(on ? t.accent : t.input).bg(on ? t.accent + "1f" : t.raised)
      .text_size(12).text_color(on ? t.foreground : t.muted).cursor_pointer()
      .hover((s) => s.bg(on ? t.accent + "2a" : t.hover).text_color(t.foreground))
      .on_click(click).child(text);
  }

  toggle(t, id, on, text, sub, click) {
    return h_flex().id(id).flex_1().gap(10).items_center().p(10).rounded(8).border_1().border_color(t.border)
      .cursor_pointer().hover((s) => s.bg(t.hover)).on_click(click)
      .child(div().w(30).h(18).flex_none().rounded(9).p(2).bg(on ? t.accent : t.input)
        .child(div().size(14).rounded(7).bg(t.background).when(on, (el) => el.ml(12))))
      .child(v_flex().min_w(0)
        .child(div().text_size(12.5).text_color(t.foreground).child(text))
        .child(div().text_size(11.5).text_color(t.dim).child(sub)));
  }

  button(t, id, text, { primary, danger, disabled, icon, click }) {
    return h_flex().id(id).h(32).px(14).gap(6).items_center().rounded(7).text_size(12.5).font_medium()
      .when(primary, (el) => el.bg(t.solid).text_color(t.solid_foreground))
      .when(!primary, (el) => el.border_1().border_color(danger ? t.blocked + "66" : t.input).bg(t.raised).text_color(danger ? t.blocked : t.foreground))
      .when(disabled, (el) => el.opacity(0.45))
      .when(!disabled, (el) => el.cursor_pointer().hover((s) => (primary ? s.opacity(0.88) : s.bg(t.hover))).on_click(click))
      .when(!!icon, (el) => el.child(svg(I(icon)).size(13)))
      .child(text);
  }

  render() {
    const t = theme();
    const cron = this.cron.value().trim();
    const next = this.next;
    const mode = MODES.find((m) => m[0] === this.mode) ?? MODES[0];
    const busy = (k, text) => (this.busy === k ? `${text}…` : text);
    return v_flex()
      .id("sched-task")
      .track_focus(this.handle)
      .on_key_down((e, cx) => this.key(e, cx))
      .w_full().min_h(0).p(18).bg(t.popover).text_color(t.foreground)
      .child(h_flex().items_center()
        .child(div().flex_1().text_size(15).font_semibold().child(this.id ? "Edit scheduled task" : "New scheduled task"))
        .when(this.running, (el) => el.child(h_flex().gap(6).mr(10).items_center().text_size(12).text_color(t.muted)
          .child(svg(I("spinner")).size(12).text_color(t.accent).spin(900)).child("Running")))
        .child(div().id("st-esc").px(6).py(2).rounded(5).text_size(11).text_color(t.dim).cursor_pointer()
          .hover((s) => s.bg(t.hover).text_color(t.foreground)).on_click(() => this.close()).child("Esc")))
      .child(v_flex().id("st-body").min_h(0).flex_shrink(1).overflow_y_scroll()
        .child(this.label(t, "Name"))
        .child(this.input(t, this.name, "name"))
        .child(this.label(t, "Prompt"))
        .child(Textarea.new(this.prompt).px(10).py(8).rounded(7).border_1()
          .border_color(this.focus === "prompt" ? t.accent : t.input).bg(t.background).text_size(13).text_color(t.foreground))
        .child(this.label(t, "Schedule", "cron: minute hour * * weekday, local time"))
        .child(h_flex().gap(6).flex_wrap()
          .child(div().w(150).child(this.input(t, this.cron, "cron")))
          .children(PRESETS.map(([text, c], i) => this.chip(t, `st-preset-${i}`, text, cron === c, (_e, cx) => { this.cron.set_value(c); this.preview(cx); cx.notify(); }))))
        .child(div().pt(6).text_size(11.5).text_color(next?.error ? t.blocked : t.dim)
          .child(next?.error ? next.error : next?.next?.length ? `Next: ${next.next.map(fmtNext).join("  ·  ")}` : " "))
        .child(this.label(t, "Folder", "the session runs here"))
        .child(h_flex().gap(6)
          .child(div().flex_1().child(this.input(t, this.folder, "folder")))
          .child(this.button(t, "st-pick", "Choose…", { icon: "folder", click: (_e, cx) => cx.spawn(async (cx) => {
            const p = await pickFolder({ prompt: "Folder the task runs in" }).catch(() => null);
            if (p) { this.folder.set_value(tilde(p)); cx.notify(); }
          }) })))
        .child(this.label(t, "Permission mode", mode[2]))
        .child(h_flex().gap(6).flex_wrap()
          .children(MODES.map(([v, text], i) => this.chip(t, `st-mode-${i}`, text, this.mode === v, (_e, cx) => { this.mode = v; cx.notify(); }))))
        .child(this.label(t, "Model"))
        .child(h_flex().gap(6).flex_wrap()
          .children(MODELS.map(([v, text], i) => this.chip(t, `st-model-${i}`, text, this.model === v, (_e, cx) => { this.model = v; cx.notify(); }))))
        .child(h_flex().pt(16).gap(8)
          .child(this.toggle(t, "st-chrome", this.chrome, "Chrome", "Let it use your browser", (_e, cx) => { this.chrome = !this.chrome; cx.notify(); }))
          .child(this.toggle(t, "st-enabled", this.enabled, this.enabled ? "On" : "Paused", this.enabled ? "Runs on schedule" : "Only runs when you press Run now", (_e, cx) => { this.enabled = !this.enabled; cx.notify(); }))))
      .when(!!this.error, (el) => el.child(div().pt(12).text_size(12).text_color(t.blocked).child(this.error)))
      .when(!!this.flash, (el) => el.child(div().pt(12).text_size(12).text_color(t.muted).child(this.flash)))
      .child(h_flex().pt(16).gap(8).items_center()
        .when(!!this.id && !this.confirmDelete, (el) => el.child(this.button(t, "st-delete", "Delete", { danger: true, disabled: !!this.busy, click: (_e, cx) => { this.confirmDelete = true; cx.notify(); } })))
        .when(this.confirmDelete, (el) => el
          .child(div().text_size(12).text_color(t.blocked).child("Delete this task?"))
          .child(this.button(t, "st-delete-yes", busy("delete", "Delete"), { danger: true, disabled: !!this.busy, click: (_e, cx) => this.act("delete", cx) }))
          .child(this.button(t, "st-delete-no", "Keep", { click: (_e, cx) => { this.confirmDelete = false; cx.notify(); } })))
        .child(div().flex_1())
        .child(this.button(t, "st-run", this.running ? "Running…" : busy("run", "Save & run now"), { icon: "terminal", disabled: !!this.busy || this.loading || this.running, click: (_e, cx) => this.act("run", cx) }))
        .child(this.button(t, "st-save", busy("save", "Save"), { primary: true, disabled: !!this.busy || this.loading, click: (_e, cx) => this.act("save", cx) })));
  }
}
