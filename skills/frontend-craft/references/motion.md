# Motion Recipes

Concrete defaults for UI motion. The project's motion tokens win when they exist; when they do not, start here. Decide first whether something should move at all: frequent actions get instant feedback, rare moments may get an authored transition. `design-with-judgment` owns the motion thesis for open-direction work; `text-animation` owns text motion.

## Contents

- Should it animate
- Tokens
- Durations
- Recipes
- Performance
- Accessibility
- Review

## Should it animate

| Frequency of the interaction | Motion |
| --- | --- |
| Constant (typing, keyboard navigation, every hover in a list) | none, or ≤150ms color/opacity |
| Frequent (buttons, toggles, tabs, menus) | short, functional, 100–250ms |
| Occasional (modals, drawers, page or panel change) | clear spatial transition, 200–500ms |
| Rare (onboarding, success, first visit) | room for one authored moment |

Every animated state change also has a static cue (color, icon, label); motion is never the only feedback.

## Tokens

```css
:root {
  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);      /* entering, responding to input */
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);  /* moving between two on-screen positions */
  --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);   /* sheets and drawers */
  --dur-press: 120ms;
  --dur-small: 160ms;   /* tooltips, small popovers */
  --dur-menu: 200ms;    /* dropdowns, selects */
  --dur-panel: 300ms;   /* modals, drawers, panels */
}
```

Never use `ease-in` for UI: it delays the first movement, exactly when the user is watching. Springs (when a motion library is already in the project) use `bounce: 0` for UI controls.

## Durations

| Element | Duration |
| --- | --- |
| Button press feedback | 100–160ms |
| Tooltip, small popover | 125–200ms |
| Dropdown, select, menu | 150–250ms |
| Modal, drawer, sheet | 200–500ms |
| Toast | ~400ms |

Exits are faster and quieter than entrances (roughly 60–75% of the enter duration, a small fixed offset instead of full travel).

## Recipes

- **Press:** `transform: scale(0.97)` on `:active` with `transition: transform var(--dur-press) var(--ease-out)`. Never smaller than `0.95`.
- **Popover and menu enter:** from `opacity: 0; transform: scale(0.96) translateY(-4px)` with `transform-origin` at the trigger side; never from `scale(0)`. With CSS only, use `@starting-style` plus `transition-behavior: allow-discrete` for `display`/`popover` entry.
- **Tooltip:** delay the first one (~400–600ms), show subsequent peers instantly (`transition-duration: 0ms` while a group is "warm").
- **Icon swap:** keep both icons, cross-fade with `opacity 0→1`, `scale 0.25→1`, `filter: blur(4px)→0`, about 200ms.
- **List item enter/exit:** small `translateY(4–8px)` plus opacity; stagger only infrequent entrances (~50–100ms per item, cap the total under ~400ms).
- **Imperfect crossfade:** add `filter: blur(2px)` during the transition to hide the seam.
- **Skip on first render:** no enter animation on initial page load for elements that are simply present (`initial={false}` on `AnimatePresence`).
- **Page and view change:** use the View Transitions API where supported (see [modern-css.md](modern-css.md)), with a plain swap as fallback.

## Performance

- Animate `transform` and `opacity`; `filter`, `clip-path`, `backdrop-filter` only when profiling shows smooth frames.
- List exact properties in `transition`; never `all`.
- `will-change` only on `transform`/`opacity`/`filter`, only after seeing first-frame stutter, removed when idle.
- Prefer CSS transitions (interruptible) for state; keyframes for one-shot sequences; Web Animations API for programmatic control; a JS library only when the project already uses one or springs/gestures need it.
- Update `transform` on the moving element directly instead of a CSS variable on a large parent.

## Accessibility

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

Prefer a designed reduced variant (opacity-only, no travel) over removing feedback entirely. Autoplaying motion longer than 5s alongside content needs pause/stop. Gestures (drag, swipe) have a click and keyboard alternative.

## Review

Replay motion at 10% speed (devtools Animations panel) and walk every state: hover, focus, active, loading, empty, interrupted mid-transition. What feels off slowed down is what is subtly wrong at full speed. Check that the element moves from where it physically comes from and that interrupting it reverses smoothly.
