// Screenshot matrix plus cheap automated floor checks for one page.
// usage: node shots.mjs <url or file> <out-dir> [--viewports 390x844,1440x900] [--schemes light,dark] [--full]
// Without a local Playwright: npx -y -p playwright@1.59.0 node shots.mjs ...
import { createRequire } from "node:module";
import { existsSync, mkdirSync, realpathSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const options = {};
const positionals = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--viewports" || args[i] === "--schemes") options[args[i].slice(2)] = args[++i];
  else if (args[i].startsWith("--")) options[args[i].slice(2)] = true;
  else positionals.push(args[i]);
}
const flag = (name, fallback) => options[name] ?? fallback;
const [target, outDir] = positionals;
if (!target || !outDir) {
  console.error("usage: node shots.mjs <url or file> <out-dir> [--viewports 390x844,768x1024,1440x900,1920x1080] [--schemes light,dark] [--full]");
  process.exit(2);
}
const viewports = flag("viewports", "390x844,768x1024,1440x900,1920x1080").split(",").map((v) => {
  const [width, height] = v.split("x").map(Number);
  return { width, height, name: v };
});
const schemes = flag("schemes", "light,dark").split(",");
const fullPage = options.full === true;
const url = /^[a-z]+:\/\//i.test(target) ? target : pathToFileURL(resolve(target)).href;

function loadPlaywright() {
  const roots = [join(process.cwd(), "package.json")];
  try {
    const bin = execFileSync("sh", ["-c", "command -v playwright"], { encoding: "utf8" }).trim();
    if (bin) roots.push(join(dirname(realpathSync(bin)), "package.json"));
  } catch {}
  for (const root of roots) {
    try {
      return createRequire(root)("playwright");
    } catch {}
  }
  console.error("Playwright not found. Run: npx -y -p playwright@1.59.0 node " + process.argv[1] + " " + args.join(" "));
  process.exit(3);
}

const { chromium } = loadPlaywright();
let browser;
try {
  browser = await chromium.launch();
} catch (error) {
  console.error(String(error).split("\n")[0]);
  console.error("Browser missing. Run: npx -y playwright@1.59.0 install chromium");
  process.exit(3);
}

mkdirSync(outDir, { recursive: true });
const report = { url, shots: [], checks: [] };

// Runs in the page: returns floor violations a script can see.
const pageChecks = () => {
  const out = [];
  const doc = document.documentElement;
  if (doc.scrollWidth > doc.clientWidth + 1) out.push(`horizontal overflow: ${doc.scrollWidth}px > ${doc.clientWidth}px`);
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    if (cs.transitionProperty.split(",").some((p) => p.trim() === "all") && parseFloat(cs.transitionDuration) > 0) {
      out.push(`transition: all on ${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/)[0] : ""}`);
      break;
    }
  }
  if (innerWidth < 768) {
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (parseFloat(getComputedStyle(el).fontSize) < 16) { out.push("mobile input font-size < 16px (iOS zoom)"); break; }
    }
  }
  for (const el of document.querySelectorAll("button, a[href], [role=button]")) {
    const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").trim();
    if (!name && !el.querySelector("img[alt]:not([alt=''])")) { out.push(`interactive element without accessible name: ${el.outerHTML.slice(0, 80)}`); break; }
  }
  const imgs = [...document.querySelectorAll("img")].filter((i) => !i.hasAttribute("alt"));
  if (imgs.length) out.push(`${imgs.length} <img> without alt`);
  const small = [...document.querySelectorAll("button, a[href], input, select, [role=button]")].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24);
  });
  if (small.length) out.push(`${small.length} interactive targets under 24px`);
  if (!document.title.trim()) out.push("empty <title>");
  if (!document.querySelector('meta[name="viewport"]')) out.push("missing <meta name=viewport> (phones render at desktop width)");

  // SVG text that falls outside its svg box is clipped (typically preserveAspectRatio="... slice").
  for (const svg of document.querySelectorAll("svg")) {
    const box = svg.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const cut = [...svg.querySelectorAll("text")].find((t) => {
      const r = t.getBoundingClientRect();
      return r.width && (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1);
    });
    if (cut) { out.push(`svg text clipped: "${cut.textContent.trim().slice(0, 40)}"`); break; }
  }

  // Text contrast: resolve colors through a canvas (handles oklch, color-mix), composite over solid ancestor backgrounds.
  const ctx = Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
  const rgba = (c) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top, under) => top.slice(0, 3).map((v, i) => v * top[3] + under[i] * (1 - top[3])).concat(1);
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const backdrop = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage !== "none") return null; // gradient or image: cannot judge statically
      const c = rgba(cs.backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    return layers.reverse().reduce((under, top) => over(top, under), [255, 255, 255, 1]);
  };
  const low = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("svg, [aria-hidden=true], :disabled, [inert]")) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || cs.visibility !== "visible" || parseFloat(cs.opacity) === 0) continue;
    const bg = backdrop(el);
    if (!bg) continue;
    const fg = over(rgba(cs.color), bg);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(cs.fontSize);
    const need = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700) ? 3 : 4.5;
    if (ratio < need) low.push(`"${el.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}:1`);
  }
  if (low.length) out.push(`${low.length} text elements below contrast minimum, e.g. ${low.slice(0, 3).join("; ")}`);
  return out;
};

for (const scheme of schemes) {
  for (const vp of viewports) {
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, colorScheme: scheme, deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text().slice(0, 200)));
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
    await page.goto(url, { waitUntil: "networkidle" }).catch((e) => errors.push(`navigation: ${e.message.split("\n")[0]}`));
    await page.waitForTimeout(400);
    const file = join(outDir, `${vp.name}-${scheme}.png`);
    await page.screenshot({ path: file, fullPage });
    const found = await page.evaluate(pageChecks).catch((e) => [`checks failed: ${e.message}`]);
    report.shots.push(file);
    report.checks.push({ viewport: vp.name, scheme, consoleErrors: errors, findings: found });
    await context.close();
  }
}

// Reduced motion at the first viewport: content must be visible without animation.
{
  const vp = viewports[0];
  const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "networkidle" }).catch(() => {});
  const file = join(outDir, `${vp.name}-reduced-motion.png`);
  await page.screenshot({ path: file, fullPage });
  report.shots.push(file);
  await context.close();
}

await browser.close();
writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 2));
const problems = report.checks.filter((c) => c.consoleErrors.length || c.findings.length);
for (const c of problems) {
  console.log(`${c.viewport} ${c.scheme}: ${[...c.consoleErrors.map((e) => "console: " + e), ...c.findings].join(" | ")}`);
}
console.log(`${report.shots.length} screenshots in ${outDir}; ${problems.length ? problems.length + " runs with findings" : "no automated findings"} (report.json)`);
process.exit(problems.length ? 1 : 0);
