---
name: frontend-craft
description: "Build, change, or review any web frontend UI to a shipped-quality floor: token plan, numeric craft floor, web-interface audit, AI-slop scan, and rendered screenshot evidence. Use for every web UI implementation or review, including fixed-spec cards; open product direction goes to design-with-judgment first."
---

# Frontend Craft

One executable path from request to a verified, rendered interface. It owns the mechanics every web UI must clear: tokens, the numeric floor, the audit checklist, the slop scan, and the evidence. It never picks the visual direction; when the direction is open, get the experience thesis from `design-with-judgment` first, then come back here.

## Authority

1. The user's words and the brief.
2. The project's own system: `DESIGN.md`/`design.md`, tokens, component library, incumbent patterns. Reuse before you invent.
3. This skill's floor, checklist, and bans. Lines marked **ban** hold even against habit; only the brief's explicit words override them.
4. Your taste, above the floor.

Accessibility, truthful content, and working controls are invariants at every level.

Never introduce a second styling system (Tailwind in a CSS project, CSS-in-JS in a Tailwind project) to apply a fix. Express every change in the project's existing language.

## Size the work

| Size | Example | Required steps |
| --- | --- | --- |
| S | one component, a spacing or state fix, copy change | 1, 3, 4, 7 (one viewport, the touched states) |
| M | one screen or flow, a component family | all steps; plan may be five lines |
| L | new surface, redesign, landing page, app shell | all steps; direction from `design-with-judgment` first |

Review-only tasks run steps 1, 4, 5, 6, 7 on the existing UI and change no source.

## Workflow

### 1. Read the project

Find the framework, styling system, tokens (`:root`, Tailwind config, theme files), component library, fonts, icon set, motion library, and the routes or stories that render the target. Use the real content and data shapes; label sample content as sample. Never invent metrics, testimonials, logos, or customer counts.

### 2. Plan (M and L)

Write a compact plan before code:

- **Color:** 4–6 named roles (background, surface, text, muted text, accent, danger), in `oklch()` when the project has no palette.
- **Type:** one or two families with roles; a modular scale (for example 1.2 or 1.25) with body ≥16px.
- **Space:** one scale (4px base: 4, 8, 12, 16, 24, 32, 48, 64, 96).
- **Shape and depth:** radius per nesting level; elevation as either border or layered shadow.
- **Motion:** which state changes animate, with tokens from [references/motion.md](references/motion.md).
- **Layout:** an ASCII wireframe per breakpoint for anything new, with alignment stated.

Then run the **default test**: describe the version any model would generate for a similar prompt (see [references/slop.md](references/slop.md)). Every plan element that matches it without a reason from the brief gets revised; say what changed. Spend boldness in one place and keep the rest quiet.

### 3. Build

- Every color, font, space, radius, shadow, and duration goes through a token. No literal color or `font-family` outside the token block.
- Cover all states for every control you touch: default, hover, focus-visible, active, disabled, loading, error, empty (and selected where it applies).
- Design empty, sparse, dense, long-content, and error states for every data view you touch.
- Mobile first; layout by flex and grid, never JS measurement. Use container queries for components that live in different widths.
- Prefer native elements and platform features before libraries; [references/modern-css.md](references/modern-css.md) lists current CSS and HTML features with their support posture.
- Copy names actions by their result ("Save changes", not "Submit"); errors say what happened and how to recover; keep one name for an action across the flow.

### 4. Self-check against the floor

Walk [references/floor.md](references/floor.md) on the built result, not the intention. Read computed values in devtools or the rendered DOM; fix every failing line before moving on.

### 5. Audit against web interface guidelines

Walk the sections of [references/web-guidelines.md](references/web-guidelines.md) that the change touches (forms, keyboard, animation, layout, content, performance, theming). Every MUST that applies passes or is reported as a blocker with the reason.

### 6. Scan for slop

Check the rendered page against [references/slop.md](references/slop.md). Remove every tell the brief did not ask for. When the page carries many tells, or the user asks to unslop, polish, or stop it looking AI-generated, run the `unslop` skill on it after this step.

### 7. Render and verify

Follow [references/verification.md](references/verification.md): screenshots at the required viewports and color schemes, keyboard pass, reduced-motion pass, long-content stress, zero console errors, and motion inspected slowed down. Look at every screenshot before claiming a pass. A fabricated pass is a defect.

### 8. Deliver

Lead with the result. Then a short receipt:

```text
Floor: <n> checks, <failures fixed> fixed, <open> open (list)
Guidelines: sections audited <list>; open MUSTs <list or none>
Slop: tells removed <list or none>
Evidence: <screenshot paths per viewport/scheme>, keyboard ✓/✗, reduced motion ✓/✗, console errors <n>
Not verified: <what and why, or none>
```

## Delegation

- **Implementation cards (Codex and other implementers):** the card's acceptance includes "按 frontend-craft 第 4–6 步自查，回执附 floor 与 guidelines 结果". Implementers without a browser report the static checks and leave screenshots to the accepting session; the card says which side shoots.
- **Accepting session:** run step 7 yourself or hand screenshot paths plus the floor's visual lines to a one-off subagent that returns pass/fail per line; keep images out of long-lived context.
- **Reviewers:** report findings as `severity | file:line | before | after | why`, severities `HIGH` (inaccessible, misleading, unreadable, broken), `MEDIUM` (noticeable usability or consistency issue), `LOW` (polish). Verdict `Block` with any HIGH, `Needs changes` with MEDIUM/LOW, `Approve` only when nothing actionable remains. Never pad.

## Related skills

- `design-with-judgment`: experience thesis, flows, direction when open; critique of product fit.
- `unslop`: deep de-slop pass ordered spacing → color → icons → copy → type → layout → details → visuals → motion.
- `text-animation`: text reveals, changing labels, animated numbers, streaming text.

## References

- [references/floor.md](references/floor.md): numeric craft floor, the hard checks.
- [references/web-guidelines.md](references/web-guidelines.md): Vercel Web Interface Guidelines (MUST/SHOULD/NEVER).
- [references/motion.md](references/motion.md): durations, easing tokens, press and enter/exit recipes.
- [references/slop.md](references/slop.md): the generated-UI defaults to refuse and their fixes.
- [references/modern-css.md](references/modern-css.md): current platform features and when to use them.
- [references/verification.md](references/verification.md): screenshot matrix, tools, and the evidence contract.
- [references/sources.md](references/sources.md): upstream sources, versions, and licenses.
