// 项目侧栏：项目 → 会话 → 子 agent。
// 项目清单在同目录 projects.json；一个项目可以绑定多个文件夹（git 仓库），其中一个是主项目
// （`main`，不写就是第一个），新会话开在主项目里。文件夹和它的 worktree 里跑着的每个主会话
// （Claude Code / Codex / OpenCode / Pi / AGY，“+” 时选）是一行，下面是它派出去的子 agent（Claude Code / Codex / opencode / pi / Gemini / AGY）
// 和各自在做什么。每行右侧的小点是状态：转圈＝运行中，blocked 色＝等你，
// done 色＝已完成，空心＝空闲；回合已结束、只剩后台 shell 在跑的算已完成，点左边标“后台 N”。
// 不属于任何项目的会话归到“其他”。
// 会话名下面一行是它所在的工作树（分支）、这条分支的 PR 和 CI 状态；子 agent 有自己的工作树时
// 同样在它下面显示。母子可以有任意多层，有下级的每一行都能收起。
// 会话行右侧的归档按钮：先列出会做什么（结束哪些子 agent、归还哪些工作树、关哪些 PR），确认后由
// hc-archive 执行，再关掉这个会话的标签页。最下面一栏有两个标签页：
// “用量”是各 agent 的剩余额度和今日用量（hc-usage）；“定时任务”是本机会自己按时跑的任务（hc-schedules：
// Claude Code 会话内排的任务、hc-task 的定时任务、Claude 桌面版的定时任务、Codex 自动化、launchd），按来源分组，组内按下次运行时间排，暂停的收在最下面。
// 不在这里启停；hc-task 的任务点一下打开它的常驻会话，悬停看最近一次结果。

import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex, ScrollHandle } from "gpui-base";
import { spaces, panes, agents, terminals, setAgentOrder, run, openOverlay, exec, theme, window, layout, home, appHome, readTextFile, writeTextFile, pickFolder, notify, checking } from "herdr";

const ICON = (name) => `mods/workspace-chrome/${name}.svg`;
const CONFIG = () => `${appHome()}/mods/projects/projects.json`;
const REFRESH = 20000; // worktree 清单多久重读一次（窗口在前台时）
const PR_EVERY = 60000; // 每条分支的 PR / CI 多久问一次 GitHub
const TRUNK = new Set(["main", "master", "prod", "HEAD"]); // 这些分支不查 PR
const PR_WORD = { OPEN: "open", DRAFT: "draft", MERGED: "merged", CLOSED: "closed" };
const CI_WORD = { pass: "CI passed", fail: "CI failed", running: "CI running" };
const USAGE_EVERY = 120000; // 用量多久重算一次
const SCHED_EVERY = 60000; // 定时任务清单多久重读一次
const BIN = () => `${home()}/.local/bin`;
const OTHER = "\u0000other";
const WORD = { working: "Working", blocked: "Needs you", done: "Done", idle: "Idle" };
// 去掉 agent 放在标题前面的状态符号或转圈字符。
const clean = (s) => (s ?? "").replace(/^[^\p{L}\p{N}\p{Ps}\p{Pi}]+/u, "").replace(/^\[ [!.] \] /, "").trim();
// Claude Code 自己上报状态时，回合已结束但还有后台 shell 在跑，会一直报 working + "N shell(s) still running"。
// 这时主 agent 其实在等你，按“已完成”显示，旁边标出后台还剩几个 shell。
const BG_SHELLS = /^(\d+) shells? still running$/;
function shown(status, report) {
  const n = status === "working" ? BG_SHELLS.exec(report?.msg ?? "")?.[1] : null;
  return n ? { status: "done", bg: Number(n) } : { status, bg: 0 };
}

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}
const narrow = () => (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;
const foldKey = (a) => `session:${a.address}`; // 会话的子 agent 是否收起，和项目的折叠记在一起
const trim = (p) => (p.length > 1 ? p.replace(/\/+$/, "") : p);
const expand = (p) => trim(p === "~" ? home() : p.startsWith("~/") ? home() + p.slice(1) : p);
const tilde = (p) => (p === home() ? "~" : p.startsWith(home() + "/") ? "~" + p.slice(home().length) : p);
const base = (p) => trim(p).split("/").pop();
const inside = (path, dir) => path === dir || path.startsWith(dir + "/");

// `git worktree list --porcelain` → [{ path, branch, main }]，第一项是主检出。
function parseWorktrees(out) {
  const list = [];
  for (const block of out.split(/\n\n+/)) {
    const item = {};
    for (const line of block.split("\n")) {
      if (line.startsWith("worktree ")) item.path = trim(line.slice(9));
      else if (line.startsWith("branch ")) item.branch = line.slice(7).replace(/^refs\/heads\//, "");
      
      else if (line.startsWith("prunable")) item.gone = true;
      else if (line === "bare") item.gone = true;
    }
    if (item.path && !item.gone) list.push(item);
  }
  if (list.length) list[0].main = true;
  return list;
}

// statusCheckRollup → pass / fail / running / null（没有检查）。
function ciOf(checks) {
  const got = (checks ?? []).map((c) => (c.status && c.status !== "COMPLETED" ? "PENDING" : c.conclusion ?? c.state ?? "PENDING"));
  if (!got.length) return null;
  if (got.some((x) => ["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"].includes(x))) return "fail";
  return got.some((x) => ["PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS"].includes(x)) ? "running" : "pass";
}

const amount = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(Math.round(n ?? 0)));
const money = (n, cur) => `${{ CNY: "¥", USD: "$" }[cur] ?? ""}${Number(n).toFixed(2)}`;
const when = (secs) => { const d = new Date(secs * 1000), p = (x) => String(x).padStart(2, "0"); return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };

export default class Projects extends View {
  init(_props, cx) {
    this.projects = []; // [{ name, folders: [绝对路径] }]
    this.raw = null; // projects.json 的原文，写回时用来防止覆盖别人的修改
    this.trees = {}; // 文件夹 → parseWorktrees 的结果，null：不是 git 仓库
    this.git = {}; // 终端的当前目录 → { path: 工作树根, branch }，null：不在 git 仓库里
    this.prs = {}; // “工作树根\n分支” → { at, pr: { number, state, ci, url } | null }
    this.asking = false;
    this.usage = null; // hc-usage 的输出
    this.sched = null; // hc-schedules 的输出
    this.schedStarted = new Set(); // 刚点了 Run now、hc-schedules 还没读到锁的任务
    this.schedHover = null;
    this.schedWatch = 0; // 打开编辑框或有任务在跑时，到这个时刻前每 5 秒重读一次
    this.tab = localStorage.getItem("projects.tab") === "sched" ? "sched" : "usage"; // 最下面一栏当前的标签页
    this.arch = null; // 正在确认或执行归档的会话：{ addr, plan, busy }
    this.error = null;
    this.scroll = ScrollHandle.new();
    this.known = "";
    this.moved = "";
    try { this.folded = new Set(JSON.parse(localStorage.getItem("projects.folded") ?? "[]")); } catch { this.folded = new Set(); }
    this.load(cx);
    cx.timer.every(REFRESH, (cx) => { if (window().active) { this.refresh(cx); this.ask(cx); } });
    this.ask(cx);
    cx.timer.every(USAGE_EVERY, (cx) => { if (window().active) this.measure(cx); });
    this.measure(cx);
    cx.timer.every(SCHED_EVERY, (cx) => { if (window().active) this.plan(cx); });
    cx.timer.every(5000, (cx) => { if (Date.now() < this.schedWatch || (this.sched ?? []).some((k) => k.running)) this.plan(cx); });
    this.plan(cx);
    // 新开或关掉空间（比如刚建了 worktree）后马上重读一次。
    cx.timer.every(1000, (cx) => {
      const now = spaces().map((s) => s.id).join();
      if (now !== this.known) { this.known = now; this.refresh(cx); }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.raw = await readTextFile(CONFIG());
        const data = JSON.parse(this.raw ?? '{"projects":[]}');
        this.projects = (data.projects ?? []).map((p) => {
          const folders = (p.folders ?? []).map(expand);
          const main = p.main ? expand(p.main) : null;
          return { name: String(p.name), folders, main: folders.includes(main) ? main : folders[0] };
        });
        this.error = null;
      } catch (e) {
        this.error = `Cannot read projects.json: ${e.message ?? e}`;
      }
      cx.notify();
      this.refresh(cx);
    });
  }

  save(cx) {
    const text = JSON.stringify({ projects: this.projects.map((p) => ({ name: p.name, ...(p.main && p.main !== p.folders[0] ? { main: tilde(p.main) } : {}), folders: p.folders.map(tilde) })) }, null, 2) + "\n";
    cx.spawn(async (cx) => {
      try {
        await writeTextFile(CONFIG(), text, this.raw);
        this.raw = text;
      } catch (e) {
        notify({ title: "Project list not saved", body: String(e.message ?? e), level: "error" });
        this.load(cx);
      }
      cx.notify();
    });
  }

  refresh(cx) {
    if (this.refreshing || checking()) return;
    this.refreshing = true;
    cx.spawn(async (cx) => {
      const folders = this.projects.flatMap((p) => p.folders);
      let next = 0;
      const work = async () => {
        while (next < folders.length) {
          const folder = folders[next++];
          try {
            const r = await exec("git", ["worktree", "list", "--porcelain"], { cwd: folder, timeout: 10000 });
            this.trees[folder] = r.code === 0 ? parseWorktrees(r.stdout) : null;
          } catch { this.trees[folder] ??= null; }
        }
      };
      await Promise.all(Array.from({ length: 4 }, work));
      this.refreshing = false;
      cx.notify();
    });
  }

  // 每个活着的终端在哪个工作树、哪条分支上；分支（主干除外）再问 GitHub 它的 PR 和检查结果。
  ask(cx) {
    if (this.asking || checking()) return;
    this.asking = true;
    cx.spawn(async (cx) => {
      const dirs = [...new Set(terminals().filter((k) => k.state === "live" && k.cwd).map((k) => trim(k.cwd)))];
      const git = {};
      await Promise.all(dirs.map(async (dir) => {
        try {
          const r = await exec("git", ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"], { cwd: dir, timeout: 8000 });
          const [path, branch] = r.code === 0 ? r.stdout.trim().split("\n") : [];
          git[dir] = path && branch ? { path: trim(path), branch } : null;
        } catch { git[dir] = null; }
      }));
      this.git = git;
      cx.notify();
      const wanted = new Map();
      for (const g of Object.values(git)) if (g && !TRUNK.has(g.branch)) wanted.set(`${g.path}\n${g.branch}`, g);
      const now = Date.now();
      await Promise.all([...wanted].filter(([key]) => now - (this.prs[key]?.at ?? 0) >= PR_EVERY).map(async ([key, g]) => {
        try {
          const r = await exec("gh", ["pr", "list", "--head", g.branch, "--state", "all", "--limit", "1", "--json", "number,state,isDraft,url,statusCheckRollup"], { cwd: g.path, timeout: 20000 });
          if (r.code !== 0) return; // 没登录、没网、不是 GitHub 仓库：保留上一次的结果
          const pr = JSON.parse(r.stdout)[0];
          this.prs[key] = { at: Date.now(), pr: pr ? { number: pr.number, url: pr.url, state: pr.isDraft && pr.state === "OPEN" ? "DRAFT" : pr.state, ci: ciOf(pr.statusCheckRollup) } : null };
        } catch { /* 下一轮再问 */ }
      }));
      for (const key of Object.keys(this.prs)) if (!wanted.has(key)) delete this.prs[key];
      this.asking = false;
      cx.notify();
    });
  }

  measure(cx) {
    if (checking()) return;
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${BIN()}/hc-usage`, [], { timeout: 60000 });
        if (r.code === 0) { this.usage = JSON.parse(r.stdout); cx.notify(); }
      } catch { /* 下一轮再算 */ }
    });
  }

  // 每个 agent 一小块：名称与今日用量一行，下面每个额度窗口一行（电量条 = 剩余），余额单独一行。
  usageRows(t) {
    const BAR = 72;
    const soon = (secs) => { const d = new Date(secs * 1000), p = (x) => String(x).padStart(2, "0");
      return secs * 1000 - Date.now() < 86400000 ? `${p(d.getHours())}:${p(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()}`; };
    const line = (id, tip) => h_flex().id(id).h(18).flex_none().pl(16).pr(10).gap(8).items_center().text_size(11).tooltip(tip);
    return (this.usage ?? []).flatMap((u, i) => {
      const spent = u.today?.cost != null ? money(u.today.cost, u.today.currency) : amount(u.today?.tokens ?? 0);
      const tip = [u.note, `Today ${amount(u.today?.tokens ?? 0)} tokens (excluding cache reads)`].filter(Boolean).join("\n");
      const rows = [h_flex().id(`usage-${u.agent}`).h(22).flex_none().px(8).gap(8).items_center().text_size(11.5).tooltip(tip).when(i > 0, (el) => el.mt(4))
        .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(u.agent))
        .child(div().flex_none().text_color(t.dim).child(`Today ${spent}`))];
      for (const q of u.quota ?? []) {
        const left = Math.max(0, 100 - q.used), color = left <= 15 ? t.blocked : left <= 40 ? t.working : t.done;
        rows.push(line(`usage-${u.agent}-${q.name}`, `${q.name}: ${q.used}% used${q.resets ? `, resets ${when(q.resets)}` : ""}`)
          .child(div().flex_none().w(34).truncate().text_color(t.muted).child(q.name))
          .child(div().flex_none().w(BAR).h(4).rounded(2).bg(t.hover).child(div().w(Math.round(BAR * left / 100)).h(4).rounded(2).bg(color)))
          .child(div().flex_none().w(34).text_color(left <= 15 ? t.blocked : t.foreground).child(`${left}%`))
          .child(div().flex_1().min_w(0))
          .child(div().flex_none().text_color(t.dim).child(q.resets ? `resets ${soon(q.resets)}` : "")));
      }
      if (u.balance) rows.push(line(`usage-${u.agent}-balance`, "Account balance")
        .child(div().flex_none().w(52).text_color(t.muted).child("Balance"))
        .child(div().flex_1().min_w(0).text_color(t.foreground).child(money(u.balance.amount, u.balance.currency))));
      if (!u.balance && !u.quota?.length) rows.push(line(`usage-${u.agent}-none`, "This agent's quota is not readable on this machine")
        .child(div().text_color(t.dim).child("No quota data")));
      return rows;
    });
  }

  plan(cx) {
    if (checking()) return;
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${BIN()}/hc-schedules`, [], { timeout: 30000 });
        if (r.code === 0) { this.sched = JSON.parse(r.stdout); cx.notify(); }
      } catch { /* 下一轮再读 */ }
    });
  }

  // 定时任务：按来源分组（hc-task / 会话里排的 / 桌面版 / Codex / launchd），组内按下次运行时间排；每行左边的点是
  // 正常 / 上次没跑或出错，右边是下次运行时间。hc-task 跑过的任务下面多一行上次结果，点行打开它的会话，
  // 点组标题右边的“全部结果”打开汇总页签。暂停的不分组，收在最下面的“已暂停”。
  schedRows(t) {
    const due = (secs) => {
      const d = new Date(secs * 1000), p = (x) => String(x).padStart(2, "0"), hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
      const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(new Date().setHours(0, 0, 0, 0))) / 86400000);
      return days <= 0 ? hm : days === 1 ? `Tomorrow ${hm}` : days < 7 ? `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()]} ${hm}` : `${d.getMonth() + 1}/${d.getDate()}`;
    };
    const WORD = { ok: "done", blocked: "needs you", error: "error", timeout: "timed out", skipped: "skipped" };
    const row = (k, on) => {
      const result = k.result ? (k.result.length > 400 ? k.result.slice(0, 400) + "…" : k.result) : null;
      const tip = [`${k.source} · ${k.when}`, k.problem, k.last ? `Last run ${when(k.last)}` : null, k.expires ? `Expires ${when(k.expires)}` : null, k.cwd ? tilde(k.cwd) : null,
        result ? `\n${result}` : null, k.open ? "Click to open its session" : null].filter(Boolean).join("\n");
      const gist = k.result?.replace(/^\s*(本次结果[：:]|Result:)\s*/, "").split("\n").find((l) => l.trim())?.trim();
      const mine = k.source === "herdr", rid = `sched-${k.source}-${k.id}`, hovered = mine && this.schedHover === rid;
      const running = mine && (k.running || this.schedStarted.has(k.id));
      const act = (id, icon, label, click) => div().id(`${rid}-${id}`).size(20).flex().flex_none().items_center().justify_center().rounded(5)
        .text_color(t.muted).cursor_pointer().hover((s) => s.bg(t.raised).text_color(t.foreground)).tooltip(label)
        .on_click((e, cx) => { cx.stop_propagation(); click(cx); }).child(svg(ICON(icon)).size(12));
      return v_flex().id(rid).w_full().flex_none().px(8).py(3).gap(1).tooltip(tip)
        .when(mine, (el) => el.on_hover((h, cx) => { this.schedHover = h ? rid : this.schedHover === rid ? null : this.schedHover; cx.notify(); }))
        .when(!!k.open, (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.openTask(k, cx)))
        .child(h_flex().w_full().h(20).gap(8).items_center().text_size(11.5)
          .child(div().size(6).rounded(3).flex_none().when(on, (el) => el.bg(k.problem ? t.blocked : t.done)).when(!on, (el) => el.border_1().border_color(t.dim)))
          .child(div().flex_1().min_w(0).truncate().text_color(on ? t.foreground : t.dim).child(k.name))
          .when(!hovered, (el) => el.child(div().flex_none().text_size(11).text_color(k.problem ? t.blocked : t.dim).child(!on ? "Paused" : k.next ? due(k.next) : k.when)))
          .when(hovered, (el) => el
            .when(!running, (el) => el.child(act("run", "terminal", "Run now", (cx) => this.runTask(k, cx))))
            .child(act("edit", "square-pen", "Edit", () => this.editTask(k.id)))))
        .when(running, (el) => el.child(h_flex().pl(14).gap(5).items_center().text_size(10.5).text_color(t.muted)
          .child(svg(ICON("spinner")).size(10).spin(900)).child("Running now")))
        .when(mine && on && !running, (el) => el.child(div().pl(14).truncate().text_size(10.5).text_color(k.problem ? t.blocked : t.dim)
          .child(k.last ? `${due(k.last)} ${WORD[k.status] ?? k.status ?? ""}${gist ? " · " + gist : ""}` : "Not run yet")));
    };
    const GROUPS = [
      ["herdr", "Scheduled (herdr)", "hc-task: fired by launchd, one long-lived session per task"],
      ["session", "In sessions", "Scheduled inside a Claude Code session with CronCreate / loop; stops when the session closes or the job expires"],
      ["claude-desktop", "Claude Desktop", "Claude Desktop scheduled tasks"],
      ["codex", "Codex automations", "Codex automations"],
      ["launchd", "System (launchd)", "Script jobs in ~/Library/LaunchAgents"],
    ];
    const all = this.sched ?? [], on = all.filter((k) => k.state === "on"), off = all.filter((k) => k.state !== "on");
    const rows = [];
    for (const [source, label, tip] of [...GROUPS, ...[...new Set(on.map((k) => k.source))].filter((x) => !GROUPS.some((g) => g[0] === x)).map((x) => [x, x, x])]) {
      const mine = on.filter((k) => k.source === source);
      if (!mine.length && source !== "herdr") continue;
      const key = `sched:group-${source}`, folded = this.folded.has(key);
      rows.push(h_flex().id(key).w_full().h(24).flex_none().mt(rows.length ? 4 : 0).px(8).gap(5).items_center().text_size(11).text_color(t.muted).tooltip(tip)
        .child(h_flex().gap(5).items_center().cursor_pointer().hover((s) => s.text_color(t.foreground)).on_click((_e, cx) => this.toggle(key, cx))
          .child(svg(ICON(folded ? "chevron-right" : "chevron-down")).size(11).flex_none()).child(div().font_medium().child(label))
          .child(div().text_color(t.dim).child(String(mine.length))))
        .when(mine.some((k) => k.problem), (el) => el.child(div().size(6).rounded(3).flex_none().bg(t.blocked)))
        .child(div().flex_1())
        .when(source === "herdr", (el) => el.child(div().id("sched-new").size(20).flex().items_center().justify_center().rounded(5).cursor_pointer()
          .text_color(t.dim).hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip("New scheduled task")
          .on_click(() => this.editTask(null)).child(svg(ICON("plus")).size(12))))
        .when(source === "herdr", (el) => el.child(div().id("sched-results").px(6).h(20).flex().items_center().rounded(5).cursor_pointer()
          .text_color(t.dim).hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip("Open the Results tab in the Scheduled space: the latest result of every task")
          .on_click((_e, cx) => this.openTask({ name: "Results", open: ["hc-task", "open"] }, cx)).child("All results"))));
      if (!folded) rows.push(...mine.map((k) => row(k, true)));
    }
    if (!on.length) rows.push(div().h(22).px(8).flex().items_center().text_size(11.5).text_color(t.dim).child("No active scheduled tasks"));
    const open = this.folded.has("sched:off-open"); // 暂停的默认收起，所以记的是“展开了”
    if (off.length) rows.push(h_flex().id("sched-off").h(24).flex_none().mt(4).px(8).gap(5).items_center().text_size(11).text_color(t.dim).cursor_pointer()
      .hover((s) => s.text_color(t.foreground)).on_click((_e, cx) => this.toggle("sched:off-open", cx))
      .child(svg(ICON(open ? "chevron-down" : "chevron-right")).size(11).flex_none()).child(`Paused ${off.length}`));
    if (open) rows.push(...off.map((k) => row(k, false)));
    return rows;
  }

  // 新建 / 编辑 hc-task：弹出 sched-task 对话框；关掉后下一轮 plan() 读到新状态。
  editTask(id) {
    this.schedWatch = Date.now() + 10 * 60000;
    openOverlay({ mod: "sched-task", kind: "dialog", modal: true, width: 640, props: id ? { id } : {} });
  }

  // 立即试跑：后台起一轮（hc-task run --detach），行上显示 Running now，跑完由 plan() 读到结果。
  runTask(k, cx) {
    this.schedStarted.add(k.id);
    this.schedWatch = Date.now() + 2 * 60000;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${BIN()}/hc-task`, ["run", k.id, "--detach"], { timeout: 20000 });
        if (r.code !== 0) this.error = `Cannot run ${k.name}: ${(r.stderr || r.stdout).trim().slice(-160)}`;
        else notify({ title: `Running ${k.name}`, body: "The result shows in the sidebar when it finishes.", level: "info", timeout: 3000 });
      } catch (e) { this.error = `Cannot run ${k.name}: ${e}`; }
      cx.timer.after(4000, (cx) => { this.schedStarted.delete(k.id); this.plan(cx); });
      this.plan(cx);
    });
  }

  // hc-task 的任务：起（或找到）它的会话终端，切到它的页签。
  openTask(k, cx) {
    cx.spawn(async (cx) => {
      try {
        const [cmd, ...args] = k.open;
        const r = await exec(`${BIN()}/${cmd}`, args, { timeout: 180000 });
        if (r.code !== 0) { this.error = `Cannot open ${k.name}: ${(r.stderr || r.stdout).trim().slice(-160)}`; cx.notify(); }
      } catch (e) { this.error = `Cannot open ${k.name}: ${e}`; cx.notify(); }
    });
  }

  setTab(tab, cx) {
    this.tab = tab;
    localStorage.setItem("projects.tab", tab);
    cx.notify();
  }

  // 归档：先问 hc-archive 会做什么，在会话行下面列出来等确认。
  askArchive(a, cx) {
    this.arch = { addr: a.address, plan: null, busy: false };
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${BIN()}/hc-archive`, [a.address, "--plan"], { timeout: 120000 });
        if (this.arch?.addr !== a.address) return;
        if (r.code === 0) this.arch.plan = JSON.parse(r.stdout);
        else { this.arch = null; this.error = `Cannot load the archive plan: ${(r.stderr || r.stdout).trim().slice(-160)}`; }
      } catch (e) { this.arch = null; this.error = `Cannot load the archive plan: ${e}`; }
      cx.notify();
    });
  }

  doArchive(a, cx) {
    this.arch.busy = true;
    cx.notify();
    cx.spawn(async (cx) => {
      let left = [];
      try {
        const r = await exec(`${BIN()}/hc-archive`, [a.address], { timeout: 600000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().slice(-160));
        left = JSON.parse(r.stdout).worktrees.filter((w) => /^(没能|could not)/.test(w.result ?? ""));
        const tab = panes().find((p) => p.terminal === a.address)?.tab;
        if (tab) run("tab.close", { id: tab, confirmed: true });
      } catch (e) { this.error = `Archive did not finish: ${e.message ?? e}`; }
      if (left.length) this.error = `Left after archiving: ${left.map((w) => `${w.branch} (${w.result})`).join("; ")}`;
      this.arch = null;
      this.refresh(cx);
      this.ask(cx);
    });
  }

  archRows(a, t) {
    const p = this.arch.plan, line = (text, tint) => div().pl(44).pr(10).py(1).text_size(11.5).text_color(tint ?? t.muted).child(text);
    if (!p) return [line("Checking what archiving will do…", t.dim)];
    const prs = p.worktrees.filter((w) => w.close_pr), kept = p.worktrees.filter((w) => w.shared), gone = p.worktrees.filter((w) => !w.shared);
    const button = (id, label, strong, on) => h_flex().id(`${id}-${a.address}`).h(22).px(9).flex_none().items_center().rounded(5).text_size(11.5).cursor_pointer()
      .bg(strong ? t.blocked : t.hover).text_color(t.foreground).hover((s) => s.opacity(0.85)).on_click((_e, cx) => { cx.stop_propagation(); on(cx); }).child(label);
    return [
      line(`Archive this session: stop ${p.terminals.length} sub-agents and close this tab`, t.foreground),
      ...gone.map((w) => line(`Return worktree ${w.branch}${w.dirty ? " (uncommitted changes, snapshot to Trash first)" : ""}`, w.dirty ? t.blocked : null)),
      ...prs.map((w) => line(`Close PR #${w.pr.number} (${w.branch}, branch kept)`, t.blocked)),
      ...p.worktrees.filter((w) => w.pr && !w.close_pr).map((w) => line(`PR #${w.pr.number} (${w.branch}) stays open`)),
      ...kept.map((w) => line(`Keep worktree ${w.branch}: another session uses it`)),
      h_flex().pl(44).pr(10).py(4).gap(8).flex_none()
        .child(this.arch.busy ? div().text_size(11.5).text_color(t.dim).child("Archiving…") : button("arch-yes", "Archive", true, (cx) => this.doArchive(a, cx)))
        .when(!this.arch.busy, (el) => el.child(button("arch-no", "Cancel", false, (cx) => { this.arch = null; cx.notify(); }))),
    ];
  }

  gitOf(all, addr) {
    const cwd = all.find((k) => k.address === addr)?.cwd;
    return cwd ? this.git[trim(cwd)] ?? null : null;
  }

  // 活着的下级总数（含下级的下级）。
  below(all, addr) {
    return all.filter((k) => k.parent === addr && k.state === "live").reduce((n, k) => n + 1 + this.below(all, k.address), 0);
  }

  // 名字下面的一行：分支、PR、CI。主干上只有分支名。
  metaRow(g, pad, t) {
    if (!g) return null;
    const known = this.prs[`${g.path}\n${g.branch}`];
    const pr = known?.pr;
    const tint = pr ? { OPEN: t.working, DRAFT: t.dim, MERGED: t.done, CLOSED: t.dim }[pr.state] : t.dim;
    const said = TRUNK.has(g.branch) ? "" : pr ? `#${pr.number} ${PR_WORD[pr.state] ?? pr.state}` : known ? "no PR" : "";
    const tip = [tilde(g.path), pr?.url, pr?.ci ? CI_WORD[pr.ci] : pr ? "No CI checks" : null].filter(Boolean).join("\n");
    return h_flex().id(`meta-${this.n++}`).h(19).flex_none().pl(pad).pr(10).gap(5).items_center().text_size(11).text_color(t.dim).tooltip(tip)
      .child(svg(ICON("git-branch")).size(11).flex_none())
      .child(div().flex_initial().min_w(24).truncate().child(g.branch === "HEAD" ? "detached HEAD" : g.branch))
      .when(Boolean(said), (el) => el.child(div().flex_none().text_color(tint).child(said)))
      .when(pr?.ci === "pass", (el) => el.child(svg(ICON("circle-check")).size(11).flex_none().text_color(t.done)))
      .when(pr?.ci === "fail", (el) => el.child(svg(ICON("x")).size(11).flex_none().text_color(t.blocked)))
      .when(pr?.ci === "running", (el) => el.child(svg(ICON("spinner")).size(11).flex_none().text_color(t.working).spin(900)))
      .when(Boolean(pr?.ci) && pr.ci !== "pass", (el) => el.child(div().flex_none().text_color(pr.ci === "fail" ? t.blocked : t.dim).child(CI_WORD[pr.ci])));
  }

  // 收起 / 展开下级的箭头，收起时带个数。
  fold(a, kids, t) {
    const shut = this.folded.has(foldKey(a));
    return h_flex().id(`fold-${a.address}`).h(22).px(3).gap(3).flex_none().items_center().rounded(5)
      .text_color(t.dim).hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip(shut ? `Show ${kids} sub-agents` : "Hide sub-agents")
      .on_click((_e, cx) => { cx.stop_propagation(); this.toggle(foldKey(a), cx); })
      .child(svg(ICON(shut ? "chevron-right" : "chevron-down")).size(11).flex_none())
      .when(shut, (el) => el.child(div().text_size(11).child(String(kids))));
  }

  // 项目 → 文件夹 → worktree，每一层带上落在那里的空间。
  model() {
    const sp = spaces(), ps = panes();
    // 所有已知检出，路径长的先比：空间落在最具体的那个里。
    const spots = [];
    this.projects.forEach((p, pi) => p.folders.forEach((folder, fi) => {
      const trees = this.trees[folder];
      spots.push({ path: folder, pi, fi, wi: -1 });
      (trees ?? []).forEach((w, wi) => { if (!w.main) spots.push({ path: w.path, pi, fi, wi }); });
    }));
    spots.sort((a, b) => b.path.length - a.path.length);
    const groups = this.projects.map((p) => ({
      name: p.name,
      main: p.main,
      folders: p.folders.map((folder) => {
        const trees = this.trees[folder] ?? [];
        return { path: folder, branch: trees.find((w) => w.main)?.branch ?? null, spaces: [], trees: trees.filter((w) => !w.main).map((w) => ({ ...w, spaces: [] })), git: this.trees[folder] !== null };
      }),
    }));
    const other = [];
    for (const s of sp) {
      if (s.machine) { other.push(s); continue; }
      const tabs = new Set((s.tabs ?? []).map((t) => t.id));
      const cwd = ps.find((p) => tabs.has(p.tab) && p.cwd)?.cwd;
      const hit = [cwd, s.folder].filter(Boolean).map(trim).map((at) => spots.find((k) => inside(at, k.path))).find(Boolean);
      if (!hit) {
        // worktree 已归还但会话还开着：路径不在清单里了，按 ~/.herdr/worktrees/<仓库名>/… 归回原项目。
        const repo = [cwd, s.folder].filter(Boolean).map((at) => /\/\.herdr\/worktrees\/([^/]+)\//.exec(trim(at) + "/")?.[1]).find(Boolean);
        const home = repo && groups.flatMap((g) => g.folders).find((f) => trim(f.path).split("/").pop() === repo);
        (home ? home.spaces : other).push(s);
        continue;
      }
      const folder = groups[hit.pi].folders[hit.fi];
      // spots 里的 wi 按完整清单编号，folder.trees 去掉了主检出。
      const tree = hit.wi < 0 ? null : folder.trees.find((w) => w.path === hit.path);
      (tree ?? folder).spaces.push(s);
    }
    return { groups, other };
  }

  // 让 space.next / space.prev 和侧栏从上到下的顺序一致。
  reorder(ids) {
    const order = spaces().filter((s) => !s.machine).map((s) => s.id);
    const key = JSON.stringify([order, ids]);
    if (ids.length !== order.length || order.every((id, i) => id === ids[i]) || key === this.moved) return;
    this.moved = key;
    for (let i = 0; i < ids.length; i++) {
      const from = order.indexOf(ids[i]);
      if (from === i) continue;
      run("space.move", { id: ids[i], index: i + 1 });
      order.splice(i, 0, order.splice(from, 1)[0]);
    }
  }

  toggle(key, cx) {
    this.folded.has(key) ? this.folded.delete(key) : this.folded.add(key);
    localStorage.setItem("projects.folded", JSON.stringify([...this.folded]));
    cx.notify();
  }

  addFolder(project, cx) {
    cx.spawn(async (cx) => {
      const picked = await pickFolder({ prompt: project ? `Add to "${project.name}"` : "Create project" });
      if (!picked) return;
      const folder = trim(picked);
      if (this.projects.some((p) => p.folders.includes(folder))) {
        notify({ title: "This folder is already in a project", body: tilde(folder) });
        return;
      }
      const target = project ? this.projects.find((p) => p.name === project.name) : null;
      if (target) target.folders.push(folder);
      else this.projects.push({ name: base(folder), folders: [folder], main: folder });
      this.save(cx);
      this.refresh(cx);
    });
  }

  // 右侧的小点：运行中转圈，等你/已完成是实心点，空闲是空心点。
  // bg：回合已结束、后台还在跑的 shell 数，点左边加一个小字“后台 N”。
  dot(status, t, bg = 0) {
    const tip = (WORD[status] ?? "Idle") + (bg ? `, ${bg} background shells still running` : "");
    const mark = status === "working"
      ? div().id(`dot-${this.n++}`).flex_none().tooltip(tip).child(svg(ICON("spinner")).size(12).text_color(t.working).spin(900))
      : div().id(`dot-${this.n++}`).size(8).rounded(4).flex_none().tooltip(tip)
        .when(status === "blocked" || status === "done", (el) => el.bg(t[status]))
        .when(status !== "blocked" && status !== "done", (el) => el.border_1().border_color(t.dim));
    if (!bg) return mark;
    return h_flex().flex_none().gap(4).items_center()
      .child(div().flex_none().text_size(10.5).text_color(t.dim).child(`bg ${bg}`)).child(mark);
  }

  pic(program, t) {
    if (!program?.icon) return svg(ICON("terminal")).size(13).flex_none().text_color(t.muted);
    return program.tint ? svg(program.icon).size(14).flex_none().text_color(t.foreground) : image(program.icon).size(14).flex_none();
  }

  // 一个会话：中控 agent。名字先取你给标签页起的名字（右键 → Rename，或在标签栏上改），没起过（还是编号，
  // 或建空间时默认的“主控”）才取 agent 自己报的标题；小字是它所在的文件夹或 worktree。
  sessionRow(a, where, t, kids = 0) {
    const told = clean(a.title);
    const plain = !told || told === a.address || /^[~/]/.test(told) || told.includes("@") || told === a.program?.name;
    const named = a.tab_name && !/^\d+$/.test(a.tab_name) && !["主控", "Main"].includes(a.tab_name);
    const label = named ? a.tab_name : plain ? (/^\d+$/.test(a.tab_name) ? (a.program?.name ?? a.tab_name) : a.tab_name) : told;
    const tab = panes().find((p) => p.terminal === a.address)?.tab;
    return h_flex().id(`session-${a.address}`).h(32).flex_none().pl(22).pr(10).gap(8).items_center().rounded(7).cursor_pointer()
      .when(a.focused, (el) => el.bg(t.selected)).when(!a.focused, (el) => el.hover((s) => s.bg(t.hover)))
      .on_click(() => run("pane.focus", { terminal: a.address }))
      .on_mouse_down("right", () => openOverlay({ mod: "menu", kind: "popup", width: 200, height: 260,
        props: tab ? { tab: "tab", target: { id: tab, space: a.space } } : { tab: "space", target: { id: a.space } } }))
      .child(this.pic(a.program, t))
      .child(div().flex_initial().min_w(48).truncate().text_color(a.focused ? t.foreground : t.muted).child(label))
      .when(kids > 0, (el) => el.child(this.fold(a, kids, t)))
      .child(div().flex_1())
      .when(Boolean(where), (el) => el.child(div().flex_initial().max_w(96).truncate().text_size(11).text_color(t.dim).child(where)))
      .child(div().id(`archive-${a.address}`).size(20).flex_none().flex().items_center().justify_center().rounded(5).text_color(t.dim)
        .hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip("Archive session: stop sub-agents, return worktrees, close sub-agent PRs")
        .on_click((_e, cx) => { cx.stop_propagation(); this.askArchive(a, cx); }).child(svg(ICON("archive")).size(12)))
      .child(this.dot(this.eff(a).status, t, this.eff(a).bg));
  }

  // 主会话显示用的状态：AgentInfo 不带原始上报，从 terminals() 的 report 里取。
  eff(a) { return shown(a.status, this.reports?.get(a.address)); }

  // 会话下面的子 agent：有标题时先显示它在做什么，终端名（续派用的名字）退到右侧小字；没有标题就显示终端名。
  kidRows(all, listed, addr, depth, t, up) {
    return all.filter((k) => k.parent === addr && k.state === "live").flatMap((k) => {
      const st0 = shown(listed.get(k.address)?.status ?? k.status, k.report);
      const st = st0.status === "error" ? "blocked" : st0.status;
      const who = k.name || k.template || k.program?.name || k.agent || k.address;
      const doing = clean(listed.get(k.address)?.title ?? k.title);
      const telling = doing && doing !== who && doing !== k.address && !/^[~/]/.test(doing);
      const g = this.gitOf(all, k.address), under = this.below(all, k.address);
      // 和上级不在同一个工作树：单独显示它的分支、PR、CI。上级的工作树不明（目录已删）时，主干上的下级不逐个重复。
      const own = g && g.path !== up?.path && (up || !TRUNK.has(g.branch)) ? g : null;
      return [h_flex().id(`sub-${k.address}`).h(26).flex_none().pl(30 + depth * 14).pr(10).gap(7).items_center().rounded(7).cursor_pointer()
        .text_size(12).hover((s) => s.bg(t.hover))
        .on_click(() => (listed.has(k.address) ? run("pane.focus", { terminal: k.address }) : run("terminal.peek", { terminal: k.address })))
        .child(this.pic(k.program, t))
        .child(div().flex_initial().min_w(48).truncate().text_color(t.muted).child(telling ? doing : who))
        .when(under > 0, (el) => el.child(this.fold(k, under, t)))
        .child(div().flex_1())
        .when(Boolean(telling), (el) => el.child(div().flex_initial().max_w(84).truncate().text_size(11).text_color(t.dim).child(who)))
        .when(k.turn > 0, (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child(`turn ${k.turn}`)))
        .child(this.dot(st, t, st0.bg)),
      own ? this.metaRow(own, 51 + depth * 14, t) : null,
      ...(this.folded.has(foldKey(k)) ? [] : this.kidRows(all, listed, k.address, depth + 1, t, g ?? up))].filter(Boolean);
    });
  }

  // 在这个文件夹里开一个新会话：弹出 new-session 选中控 agent（Claude Code / Codex / OpenCode / Pi / AGY）。
  newSession(folder) {
    openOverlay({ mod: "new-session", kind: "popup", width: 220, height: 200, props: { folder: folder.path, space: folder.spaces[0]?.id } });
  }

  // 项目行：名字后面跟折叠箭头，最右是“＋”（在主项目里开一个新会话）。
  header(key, label, t, { count, plus, live, tip } = {}) {
    const folded = this.folded.has(key);
    return h_flex().id(`project-${key}`).h(32).flex_none().mt(4).pl(8).pr(4).gap(6).items_center().rounded(7).cursor_pointer()
      .text_color(t.muted).hover((s) => s.text_color(t.foreground))
      .when(Boolean(tip), (el) => el.tooltip(tip))
      .on_click((_e, cx) => this.toggle(key, cx))
      .child(div().flex_initial().min_w(0).truncate().font_medium().child(label))
      .child(svg(ICON(folded ? "chevron-right" : "chevron-down")).size(11).flex_none().text_color(t.dim))
      .when(folded && count > 0, (el) => el.child(div().flex_none().text_size(11.5).text_color(t.dim).child(String(count))))
      .child(div().flex_1())
      .when(folded && Boolean(live), (el) => el.child(this.dot(live, t)))
      .when(Boolean(plus), (el) => el.child(div().id(`project-${key}-plus`).size(24).flex_none().flex().items_center().justify_center().rounded(6)
        .hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip("New session: pick the agent")
        .on_click((_e, cx) => { cx.stop_propagation(); plus(cx); })
        .child(svg(ICON("plus")).size(13))));
  }

  render() {
    const t = theme();
    this.n = 0;
    const collapsed = narrow();
    const m = this.model();
    const all = terminals(), ag = agents();
    const listed = new Map(ag.map((a) => [a.address, a]));
    this.reports = new Map(all.map((k) => [k.address, k.report]));
    const live = new Set(all.filter((k) => k.state === "live").map((k) => k.address));
    const parent = new Map(all.map((k) => [k.address, k.parent]));
    // 会话＝没有（还活着的）上级的 agent；有上级的画在上级下面。
    const leads = ag.filter((a) => !live.has(parent.get(a.address)));
    const urgent = (list) => ["blocked", "done", "working"].find((st) => list.some((a) => this.eff(a).status === st));
    const ids = [], order = [], rows = [], rail = [];
    const sessions = (list, whereOf) => list.flatMap((a) => {
      order.push(a.address);
      rail.push(a);
      const g = this.gitOf(all, a.address);
      return [this.sessionRow(a, whereOf(a), t, this.below(all, a.address)), this.metaRow(g, 44, t),
        ...(this.arch?.addr === a.address ? this.archRows(a, t) : []),
        ...(this.folded.has(foldKey(a)) ? [] : this.kidRows(all, listed, a.address, 1, t, g))].filter(Boolean);
    });
    for (const g of m.groups) {
      const where = new Map();
      for (const f of g.folders) {
        for (const s of f.spaces) where.set(s.id, f.path === g.main ? "" : base(f.path));
        for (const w of f.trees) for (const s of w.spaces) where.set(s.id, ""); // 分支在名字下面那一行
      }
      ids.push(...where.keys());
      const mine = leads.filter((a) => where.has(a.space));
      const home = g.folders.find((f) => f.path === g.main) ?? g.folders[0];
      const tip = g.folders.map((f) => `${tilde(f.path)}${f === home && g.folders.length > 1 ? " (main)" : ""}`).join("\n");
      rows.push(this.header(g.name, g.name, t, { count: mine.length, live: urgent(mine), tip, plus: home ? () => this.newSession(home) : null }));
      if (this.folded.has(g.name)) { mine.forEach((a) => { order.push(a.address); rail.push(a); }); continue; }
      rows.push(...sessions(mine, (a) => where.get(a.space)));
    }
    const rest = leads.filter((a) => m.other.some((s) => s.id === a.space));
    ids.push(...m.other.map((s) => s.id));
    if (rest.length) {
      rows.push(this.header(OTHER, "Other", t, { count: rest.length, live: urgent(rest) }));
      if (this.folded.has(OTHER)) rest.forEach((a) => { order.push(a.address); rail.push(a); });
      else rows.push(...sessions(rest, (a) => a.space_name));
    }
    this.reorder(ids);
    setAgentOrder(order);
    const cells = rail.map((a) => h_flex().flex_none().justify_center().child(
      h_flex().id(`rail-${a.address}`).relative().w(32).h(28).rounded(6).justify_center().items_center().tooltip(`${a.space_name} · ${WORD[this.eff(a).status] ?? "Idle"}`)
        .when(a.focused, (el) => el.bg(t.selected)).when(!a.focused, (el) => el.hover((h) => h.bg(t.hover)))
        .on_click(() => run("pane.focus", { terminal: a.address }))
        .child(this.pic(a.program, t))
        .when(this.eff(a).status === "blocked" || this.eff(a).status === "done", (el) => el.child(div().absolute().right(3).top(3).size(6).rounded(3).bg(t[this.eff(a).status])))));
    return v_flex().size_full().min_h(0).bg(t.chrome).text_size(13)
      .child(h_flex().h(31).pl(16).pr(10).items_center().justify_between().text_size(11.5).text_color(t.dim)
        .when(collapsed, (el) => el.pl(0).pr(0).justify_center())
        .when(!collapsed, (el) => el.child("Projects"))
        .child(div().id("project-new").size(26).flex_none().flex().items_center().justify_center().rounded(6).cursor_pointer()
          .hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip("Pick a folder to create a project")
          .on_click((_e, cx) => this.addFolder(null, cx)).child(svg(ICON("folder-plus")).size(14))))
      .when(Boolean(this.error) && !collapsed, (el) => el.child(div().px(16).pb(6).text_size(11.5).text_color(t.blocked).child(this.error)))
      .child(v_flex().flex_1().min_h(0).px(collapsed ? 0 : 8).pb(14).overflow_y_scrollbar().track_scroll(this.scroll)
        .children(collapsed ? cells : rows))
      .when(!collapsed && Boolean(this.usage?.length || this.sched), (el) => {
        const running = (this.sched ?? []).filter((k) => k.state === "on");
        const tab = (id, label) => h_flex().id(`tab-${id}`).h(24).px(10).flex_none().items_center().rounded(6).cursor_pointer()
          .when(this.tab === id, (el) => el.bg(t.selected).text_color(t.foreground)).when(this.tab !== id, (el) => el.text_color(t.muted).hover((s) => s.bg(t.hover).text_color(t.foreground)))
          .on_click((_e, cx) => this.setTab(id, cx)).child(label);
        return el.child(v_flex().flex_none().px(8).pt(6).pb(8).border_t_1().border_color(t.hover)
          .child(h_flex().h(28).gap(4).pb(4).items_center().text_size(12)
            .child(tab("usage", "Usage")).child(tab("sched", `Scheduled ${running.length}`))
            .when(running.some((k) => k.problem), (el) => el.child(div().id("sched-alert").size(6).rounded(3).flex_none().bg(t.blocked).tooltip("A scheduled task missed its last run or failed"))))
          .child(v_flex().id("bottom-list").max_h(340).overflow_y_scroll().children(this.tab === "sched" ? this.schedRows(t) : this.usageRows(t))));
      });
  }
}
