# Rendered Verification

A UI task is not done until its rendered result has been looked at. Code review, type checks, and unit tests do not show spacing, contrast, overflow, or motion.

## Contents

- Evidence contract
- Screenshot matrix
- Tools
- Manual passes
- Reading the screenshots
- When you cannot render

## Evidence contract

The receipt names, for the primary path:

1. Screenshot paths per viewport and color scheme (S-size work: one viewport and the touched states).
2. Automated findings from `scripts/shots.mjs` (or the equivalent checks) and what was fixed.
3. Keyboard pass result.
4. Reduced-motion result when anything animates.
5. Console errors on load and during the path: zero, or listed with cause.
6. Anything not verified, with the reason.

## Screenshot matrix

| Size | Viewports | Schemes | States |
| --- | --- | --- | --- |
| S | the one where the change shows (usually 390 or 1440) | the active one | touched states |
| M | 390×844, 1440×900 | light, and dark if the product supports it | default, hover/focus, loading, empty, error |
| L | 390×844, 768×1024, 1440×900, 1920×1080 | both supported schemes | all of the above plus long content and reduced motion |

Stress content once per data view: empty, one item, typical, very long strings, many items.

## Tools

Use whichever the session has, in this order:

1. **The skill's script** (headless, no browser session needed):

   ```bash
   npx -y -p playwright@1.59.0 node <skill-dir>/scripts/shots.mjs <url-or-file> <out-dir> \
     [--viewports 390x844,1440x900] [--schemes light,dark] [--full]
   ```

   It writes one PNG per viewport and scheme plus a reduced-motion shot and `report.json`, and exits non-zero when it finds horizontal overflow, console errors, `transition: all`, mobile inputs under 16px, unnamed controls, images without `alt`, targets under 24px, an empty `<title>`, a missing viewport meta, SVG text clipped by its box, or text below the 4.5:1 / 3:1 contrast minimum (text over gradients or images is skipped; check those by eye). If the browser is missing it prints the one install command. Start the project's dev server first for framework apps and pass its URL.
2. **A controlled browser** (Claude in Chrome, Codex computer use, the project's Playwright setup) for interaction: hover, focus, open menus, submit forms, record motion.
3. **The project's own visual tests** (Storybook, Playwright snapshots) when they cover the surface.

Screenshots and recordings go in the task's scratch or worktree output directory, never committed unless the repository keeps visual baselines.

## Manual passes

- **Keyboard:** Tab through the primary path from the top. Every stop has visible focus, order follows reading order, Enter/Space activate, Escape closes overlays and returns focus, nothing traps focus except a modal.
- **Motion:** replay at 10% speed in devtools Animations; interrupt a transition halfway; toggle `prefers-reduced-motion` (devtools Rendering panel) and confirm content still appears.
- **Zoom:** 200% browser zoom keeps content usable without horizontal scroll on a desktop width.
- **Contrast:** check text on every surface it appears on, including hover and disabled states, with devtools or a contrast checker.
- **Ultra-wide:** 1920 or 50% zoom shows capped measure and centered container.

## Reading the screenshots

Look at each image; do not infer from the code. Check in this order: the primary action is findable in seconds; hierarchy reads at a squint; spacing rhythm is even; nothing overflows, clips, or overlaps; text wraps well (no single-word last lines in headings); dark mode has no unthemed white boxes or invisible text; mobile keeps priority order rather than just stacking.

In delegated work, the accepting session hands screenshot paths and the visual floor lines to a one-off subagent that returns pass/fail per line, so images never sit in a long-lived context.

## When you cannot render

Some sandboxes have no browser or network. Then run every static check you can (lint, types, tests, a grep for `transition: all`, literal colors, `outline: none`), state "rendered verification not run" in the receipt with the reason, and leave the screenshot step to the accepting session. Never write a pass you did not observe.
