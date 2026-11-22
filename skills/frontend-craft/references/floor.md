# Craft Floor

Hard checks on the rendered result. Each line is pass/fail; read computed values instead of trusting the source. The project's own tokens and design system win on exact values; the floor still requires that a deliberate value exists. Lines marked **ban** are not waived by habit, only by the brief's explicit words.

## Contents

- Tokens and system
- Typography
- Color and contrast
- Space and layout
- Surfaces and icons
- Interaction and states
- Motion
- Content and copy
- Browser surfaces

## Tokens and system

- Every color and font family resolves to a token declared at `:root` or a theme block; no literal color or `font-family` past it.
- One spacing scale, one radius per nesting level, one primary button style, one container width per page type.
- Elevation declared once per surface: a border **or** a layered shadow, not a 1px border under a wide soft shadow.
- Nested radii are concentric: outer radius = inner radius + padding between them.

## Typography

- Body text ≥16px; mobile `<input>` font-size ≥16px.
- Body measure 45–75ch (target ~65ch); never wider than 80ch.
- Line height: body 1.4–1.6, headings 1.05–1.25; serif body slightly looser than sans.
- At most two families (a third only for code or data); a visible scale step between levels, not 1–2px nudges.
- Display size capped (≤6rem); negative tracking no tighter than -0.04em, usually -0.01 to -0.03em on large type only.
- Headings `text-wrap: balance`; long body `text-wrap: pretty`.
- Changing or compared numbers use `font-variant-numeric: tabular-nums`.
- Real copy runs at every breakpoint without overflow; long words break (`overflow-wrap: break-word`), flex/grid children that truncate carry `min-width: 0`.

## Color and contrast

- Body and placeholder text ≥4.5:1 against its background; large text (≥24px, or ≥18.66px bold) and UI component boundaries ≥3:1. Check hover, active, focus, and disabled-but-readable states too.
- Status never relies on color alone: add an icon, label, or pattern.
- Secondary text on a colored surface is tinted from that surface or the foreground, not neutral gray.
- Dark theme: `color-scheme: dark` on `<html>`, `<meta name="theme-color">` matches the background, no pure-black-on-pure-white glare without reason.
- Charts use a color-blind-safe palette.

## Space and layout

- All gaps come from the spacing scale; related items sit tighter than unrelated groups; more space above a heading than below it.
- Alignment is deliberate to a grid or shared edge; optical correction (±1px, icon nudges) where geometry looks wrong.
- No horizontal scroll at 320px; `overflow-x: clip` on `html, body` when needed, never `100vw` widths.
- Grid tracks that hold images or long text use `minmax(0, 1fr)`.
- Container is capped and centered: at 1920px the gutters grow, measure and grids do not.
- Primary content and action of a landing hero are visible at 1280×800 without scrolling.
- Safe areas respected on mobile (`env(safe-area-inset-*)`); sticky and fixed elements never cover focused content (`scroll-margin-top`, `scroll-padding-top`).
- Images and media reserve their box (`width`/`height` or `aspect-ratio`): no layout shift on load.

## Surfaces and icons

- Shadows have an offset and soft blur, ideally layered (ambient + direct); no zero-offset colored glow as depth.
- Card radii roughly 8–16px; pills only for small controls and tags.
- Images get a 1px low-opacity outline (`oklch(0 0 0 / 0.1)` light, `oklch(1 0 0 / 0.1)` dark) when they sit on a similar background.
- One icon library per surface; stroke matches adjacent text weight (≈1.5px beside 400, 2px beside 600); icons use `currentColor` and get states from CSS. **Ban:** emoji or Unicode glyphs as an icon system.
- No card inside a card.

## Interaction and states

- Every interactive element has a visible `:focus-visible` style that appears instantly and is not clipped. **Ban:** `outline: none` without a replacement.
- Hit area ≥24×24px everywhere, ≥44×44px for touch and primary mobile controls (40×40px acceptable in dense desktop UI); extend with padding or a pseudo-element, never overlap two hit areas.
- Every control has default, hover (pointer devices only, `@media (hover: hover)`), focus-visible, active, disabled, loading, and error states as applicable; contrast rises on hover/active/focus.
- Loading buttons keep their label and width and show progress; submit stays enabled until the request starts.
- Destructive actions confirm or offer Undo.
- Data views have designed empty, loading (skeleton matching final layout), error, and long-list states.
- Navigation uses links (`<a>`), actions use `<button>`; URL reflects filters, tabs, and pagination.
- Works with keyboard only: logical tab order, visible focus, Escape closes overlays, focus returns to the trigger.

## Motion

- Only `transform`, `opacity` (and `filter`, `clip-path` when smooth) animate. **Ban:** `transition: all`; animating `width`, `height`, `top`, `left`.
- UI transitions ≤300ms (modals and drawers up to ~500ms); ease-out family for entering and responding, never `ease-in` on UI.
- `prefers-reduced-motion: reduce` gets a reduced or static variant; content is visible without JavaScript and without the animation finishing.
- Animations are interruptible (CSS transitions for state), start from the trigger's origin, and never from `scale(0)`.
- No custom animation on high-frequency actions (typing, every hover, keyboard navigation); at most one authored entrance moment per page. Details in [motion.md](motion.md).

## Content and copy

- Real or clearly labeled sample content; no invented numbers, quotes, logos, or case-study counts.
- Buttons name the outcome; errors name the problem and the fix; empty states offer the next action.
- `…` character for truncation and follow-up actions ("Rename…"); non-breaking spaces in `10 MB`, `⌘ K`.
- Dates, times, numbers, and currency through `Intl.*` with the user's locale.
- Page `<title>` matches the current context; one `<h1>`, hierarchical headings.

## Browser surfaces

- Text selection color, caret color, scrollbars (`scrollbar-color`, `scrollbar-gutter: stable` where layout jumps), focus rings, link underline offset, and native `<select>`/form controls are themed from the palette, not left at defaults that clash.
