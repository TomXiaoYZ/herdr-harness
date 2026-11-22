# Modern Platform Features

Prefer the platform before a library. Support moves fast: before relying on a feature in the second or third tier, check its current Baseline status on [web.dev/baseline](https://web.dev/baseline) or [caniuse.com](https://caniuse.com) and the project's browser targets (`browserslist`, `package.json`). Never drop a working fallback because a feature "should" be supported.

## Contents

- Tier 1: use by default
- Tier 2: use with a fallback
- Tier 3: progressive enhancement only
- Framework notes

## Tier 1: use by default

| Need | Feature | Note |
| --- | --- | --- |
| Perceptual palette, tints, hover shades | `oklch()`, `color-mix()`, relative color `oklch(from var(--accent) calc(l - 0.08) c h)` | Even lightness steps; derive states from one token |
| Light and dark from one declaration | `color-scheme: light dark` + `light-dark(<light>, <dark>)` | Also themes native controls and scrollbars |
| Component responds to its slot, not the viewport | `container-type: inline-size` + `@container (width > 30rem)` | Use for cards, sidebars, reusable widgets |
| Parent or sibling-aware styling | `:has()` | Form validity styling, layout switches; avoid huge descendant scans |
| Scoped, ordered CSS | native nesting, `@layer reset, base, components, utilities` | Layers end specificity wars |
| Aligned nested grids | `grid-template-columns: subgrid` | Card rows whose titles and footers line up |
| Mobile viewport height | `100dvh` / `100svh` instead of `100vh` | Avoids the jumping URL bar |
| Heading and paragraph wrapping | `text-wrap: balance` on headings | `text-wrap: pretty` on body where supported; harmless elsewhere |
| Overlays without z-index hacks | `<dialog>` with `showModal()`, `popover` attribute | Top layer, Escape, focus handling for free |
| Entry transitions for elements appearing | `@starting-style` + `transition-behavior: allow-discrete` | Animate `display: none` → shown, popovers, dialogs |
| Typed, animatable custom properties | `@property --angle { syntax: '<angle>'; inherits: false; initial-value: 0deg; }` | Animate gradients and custom values |
| Stable layout when scrollbars appear | `scrollbar-gutter: stable` | Stops horizontal jump on overflow toggles |
| Responsive images | `<img srcset sizes>`, `<picture>`, `loading="lazy"`, `fetchpriority="high"` on the LCP image, explicit `width`/`height` | Prevents CLS, speeds LCP |
| Locale formatting | `Intl.NumberFormat`, `Intl.DateTimeFormat`, `Intl.RelativeTimeFormat` | Never hand-format numbers or dates |

## Tier 2: use with a fallback

| Need | Feature | Fallback |
| --- | --- | --- |
| Animated page and state changes | View Transitions: `document.startViewTransition()` (same-document); `@view-transition { navigation: auto; }` for cross-document MPA | Instant swap; wrap in `if (document.startViewTransition)`; honor reduced motion |
| Tooltips and menus tethered to a trigger | CSS anchor positioning: `anchor-name`, `position-anchor`, `position-area`, `position-try-fallbacks` | Floating UI or the project's popover library |
| Scroll-linked progress and reveals | `animation-timeline: scroll()` / `view()` | Static state, or IntersectionObserver; always gate with `@supports` |
| Themed scrollbars | `scrollbar-color`, `scrollbar-width: thin` | Default scrollbars |
| Declarative buttons that open dialogs/popovers | `commandfor` + `command` attributes; `popovertarget` | A small click handler |
| Auto-growing inputs and textareas | `field-sizing: content` | Fixed rows plus JS autosize |

## Tier 3: progressive enhancement only

Use only behind `@supports` with an equivalent experience without it:

- `interpolate-size: allow-keywords` / `calc-size()` for animating to `height: auto` (fallback: grid-rows `0fr → 1fr` trick, or no height animation).
- `appearance: base-select` customizable `<select>` (fallback: native select themed with `color-scheme`, `accent-color`, explicit `background-color` and `color`).
- `text-box: trim-both cap alphabetic` for optical vertical centering in buttons and badges.
- Scroll-state container queries (`container-type: scroll-state`) for stuck headers.

## Framework notes

- **React:** prefer uncontrolled inputs for large forms; inputs with `value` need `onChange`; guard date/time rendering against hydration mismatch; virtualize lists over ~50 rows.
- **Tailwind:** express tokens in the theme (`@theme` in v4), not as arbitrary values sprinkled through markup; use `motion-safe:`/`motion-reduce:`, `@container` variants, `tabular-nums`, `text-balance`, `text-pretty`.
- **Fonts:** self-host or preload critical fonts (`<link rel="preload" as="font" crossorigin>`), `font-display: swap`, subset where possible; variable fonts over many static weights.
- **Performance budget:** LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 on a throttled mobile profile; mutations respond in <500ms or show progress.
