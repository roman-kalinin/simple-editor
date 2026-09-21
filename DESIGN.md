# Simple Video Editor — Design System

**Round 4: Linear-grade visual language.** Dark theme, near-monochrome base, one earned accent, monoline (Feather) icons, progressive disclosure. This document is implementable directly — every value below is a real hex/px/weight, meant to be pasted into `style.css :root` and referenced by role. Do not hardcode raw hex or px in components; use the tokens.

The look we are chasing: Linear / Vercel / Raycast. Structure comes from **space, weight, and opacity** — not from borders and saturated color. Color appears only where it carries meaning (accent = primary action + selection; status = ok/warn/danger; cut-red = destructive region). Everything else is drawn from a neutral surface ramp and a single ink at four opacities.

---

## 1. Design principles

1. **Color only for meaning.** The base UI is grayscale. Saturated color is reserved for: the accent (primary action, selection, focus, playhead progress), status (ffmpeg ok/bad, job states), and the cut-red destructive region. If a color isn't saying something, it's neutral. This one rule does most of the work.
2. **Separate by tone and space, not dividers.** Regions differ because they sit on slightly different surface tints with generous whitespace. When an edge is genuinely needed, it is a hairline at 6–10% white, never a hard gray line. No element wears a border *and* a shadow *and* a radius by default.
3. **Calm density.** Show a lot without feeling busy — one strict spacing scale, low-contrast separation, restrained color. Power lives behind progressive disclosure (details, hover reveal, the export modal), not all on screen at once.
4. **Restrained, fast motion.** Motion confirms and orients; it is felt, not watched. Transform + opacity only, 120–280ms, ease-out, always with a `prefers-reduced-motion` guard.
5. **Monoline iconography, never emoji.** One icon set (Feather), 16px, `stroke-width: 2`, `currentColor`. Emoji as UI glyphs is the single most recognizable "toy" tell and must be fully removed.

---

## 2. Color palette

Dark-first. Surfaces are a near-black tonal ramp (elevation by tone). Ink is **one color at four opacities**, so text tints correctly on every surface level. The accent is Linear's lavender-indigo.

```css
:root {
  color-scheme: dark;

  /* ---- Surfaces: elevation by tone, not borders ---- */
  --plane:      #08090a;   /* deepest — body background */
  --surface-0:  #0c0d0f;   /* app chrome: header, clip-strip footer */
  --surface-1:  #131417;   /* panels, cards, clip tiles, modals */
  --surface-2:  #191b1f;   /* raised / hover */
  --surface-3:  #212327;   /* active / input / pressed */
  --well:       #060708;   /* recessed wells: timeline track, video letterbox */

  /* ---- Translucent fills: primary separation mechanism ---- */
  --fill-subtle: rgba(255,255,255,0.03);
  --fill-soft:   rgba(255,255,255,0.05);
  --fill-med:    rgba(255,255,255,0.08);
  --fill-strong: rgba(255,255,255,0.12);

  /* ---- Hairlines: used sparingly ---- */
  --hairline:     rgba(255,255,255,0.07);
  --hairline-str: rgba(255,255,255,0.11);
  --ring:         0 0 0 1px rgba(255,255,255,0.10);

  /* ---- Ink: ONE color at four opacities ---- */
  --ink-1: #f4f5f7;                 /* primary — headings, active copy */
  --ink-2: rgba(244,245,247,0.64);  /* secondary — body, subheads */
  --ink-3: rgba(244,245,247,0.42);  /* muted — labels, metadata, timestamps */
  --ink-4: rgba(244,245,247,0.26);  /* faintest — disabled, placeholder */

  /* ---- Accent: Linear lavender-indigo (selection, primary, focus) ---- */
  --accent:      #5e6ad2;   /* primary CTA, progress, playhead progress */
  --accent-hi:   #6f7bea;   /* hover */
  --accent-ink:  #ffffff;   /* text on accent */
  --accent-dim:  rgba(94,106,210,0.16);  /* selection wash, active nav */
  --accent-ring: rgba(94,106,210,0.55);  /* focus ring */

  /* ---- Status: the only other saturated colors ---- */
  --good:     #3ecf8e;  --good-dim:     rgba(62,207,142,0.12);
  --warn:     #e6b450;  --warn-dim:     rgba(230,180,80,0.12);
  --danger:   #e5674e;  --danger-dim:   rgba(229,103,78,0.14);
  --danger-hi: #f07a63;

  /* ---- Domain colors: the cut/mark vocabulary ---- */
  --cut:      rgba(229,103,78,0.42);  /* cut band fill (destructive region) */
  --cut-hi:   #f07a63;                /* cut band edge / selected */
  --mark:     #e6b450;               /* pending mark line (in-progress) */

  /* ---- Shadows: fixed, soft, small set ---- */
  --shadow-1: 0 1px 2px rgba(0,0,0,0.35);
  --shadow-2: 0 1px 2px rgba(0,0,0,0.35), 0 8px 24px rgba(0,0,0,0.28);
  --shadow-3: 0 4px 12px rgba(0,0,0,0.45), 0 16px 48px rgba(0,0,0,0.40);
}
```

**Sourced Linear reference values** (for provenance): Linear's true background is `#08090A`; text ramp `#F7F8F8 / #8A8F98 / #62666D`; signature accent `#5E6AD2` with interactive `#7170FF`; borders `rgba(255,255,255,0.05–0.08)`. Our palette adopts the accent and background verbatim and uses opacity-based ink (functionally equivalent to Linear's gray ramp) for coherence across surfaces.

**Migration from current tokens:** `--bg #111 → --plane #08090a`; `--bg-2/3/4 → --surface-0/1/2`; `--border #2b2b2b → --hairline`; `--border-hi → --hairline-str`; `--accent #3b7dd8 → --accent #5e6ad2` (this single change is the biggest visual shift — the blue reads generic, the indigo reads Linear); `--text #eee → --ink-1`; `--text-dim #999 → --ink-3`.

---

## 3. Typography

```css
:root {
  --font: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, "Cascadia Code", Consolas, "SF Mono", monospace;

  /* Type ramp (px) */
  --fs-micro: 10px;   /* ruler ticks, tile duration */
  --fs-xs:    11px;   /* metadata, hints, labels */
  --fs-sm:    12px;   /* controls, body-small */
  --fs-md:    13px;   /* default body, buttons, inputs */
  --fs-lg:    15px;   /* modal titles, section headers */
  --fs-xl:    18px;   /* empty-state headline */
  --fs-2xl:   22px;   /* reserved / hero */
}
```

- **Font:** Inter if it can be bundled/inlined as a local `@font-face` (offline requirement — no Google Fonts CDN at runtime); otherwise `system-ui`. Inter is what makes it read Linear; system-ui is an acceptable fallback.
- **Weights — 3 only:** `400` (body), `500` (medium — labels, controls, emphasis), `600` (semibold — headings, primary button). Never use `700`+; the current `.icon-btn { font-weight: 700 }` and `kbd` heavy weights should drop to 500/600.
- **Negative tracking on large text:** `--fs-lg` and up get `letter-spacing: -0.01em`; `--fs-xl` gets `-0.02em`. Headings feel engineered, not loose.
- **Micro-labels:** small (10–11px), weight 500, `text-transform: uppercase`, `letter-spacing: 0.06em`, color `--ink-3`. Used for `.setting-label`, `.sc-group`, section eyebrows.
- **Tabular numerals on all data:** `font-variant-numeric: tabular-nums` on timecodes, durations, ruler ticks, stats, playhead time — so numbers don't jitter as they change. Keep the mono font for file paths and raw timecodes (it is part of the tool's identity), but add `tabular-nums`.
- **Rule:** large OR bold OR bright — not all three. Reserve full emphasis for one element per screen.

---

## 4. Spacing

One 4px-based scale. Never use an off-scale value.

```css
:root {
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px; --sp-4: 16px;
  --sp-5: 24px; --sp-6: 32px; --sp-7: 48px;
}
```

- Header padding `10px 16px → var(--sp-3) var(--sp-4)`.
- Workspace padding `12px 16px → var(--sp-3) var(--sp-4)`.
- Control gaps: within a group `--sp-1`; between groups `--sp-3`; section gaps `--sp-4`.
- Clip strip gap `10px → --sp-3`; tile inner padding `--sp-2`.
- **Generous first, then tighten** — start looser than feels necessary and remove.

---

## 5. Borders, radius, shadow

```css
:root {
  --r-sm:   6px;   /* buttons, inputs, chips, bands */
  --r-md:   8px;   /* cards, tiles, timeline track */
  --r-lg:   12px;  /* modals */
  --r-full: 999px; /* round icon buttons, pills */
}
```

- **Radii:** current 3–4px radii read cheap/sharp. Move to **6px (controls), 8px (cards/tiles/track), 12px (modals)**. Nesting rule: inner radius = outer − padding.
- **Borders:** default to *no* border. Separate by surface tone + gap. Where an edge is genuinely needed (input, track, modal), use `1px solid var(--hairline)` — a single hairline, never hairline + shadow together on the same element.
- **Focus ring:** replace the current 2px solid accent outline with the ring-shadow pattern for crispness: `box-shadow: var(--ring), 0 0 0 3px var(--accent-ring); outline: none;` (keep a real outline fallback for `:focus-visible` where box-shadow is clipped by `overflow:hidden`).
- **Shadows:** only things that actually float get one — popover/thumb-preview `--shadow-2`, modal `--shadow-3`, toasts `--shadow-2`. Flat elements get none.

---

## 6. Motion

```css
:root {
  --dur-fast: 140ms;   /* hover, press, color */
  --dur-mid:  200ms;   /* dropdowns, tooltips, disclosure */
  --dur-slow: 280ms;   /* modal, drawer */
  --ease-out:   cubic-bezier(0.23, 1, 0.32, 1);
  --ease-inout: cubic-bezier(0.77, 0, 0.175, 1);
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

- **Animate `transform` and `opacity` only.** Never width/height/top/left/margin.
- **Press:** `:active { transform: scale(0.97); }` on buttons. **Hover:** background/opacity shift over `--dur-fast`; gate any scale behind `@media (hover:hover) and (pointer:fine)`.
- **Enter/exit:** entrance = `opacity:0` + `scale(0.97)` + a few px translate, ease-out. Modal + backdrop on identical timing (`--dur-slow`). Exits ~20% faster than entrances.
- **Frequency rule:** actions hit 100+ times/day (mark cut, step frame) should have minimal or no animation — motion becomes friction. Keep the cut-added flash subtle and brief.
- Migrate `--t-fast 90ms → --dur-fast`, `--t-med 160ms → --dur-mid`, `--ease → --ease-out`.

---

## 7. Icons — Feather

- **Set:** [Feather](https://feathericons.com). Monoline, geometric, offline.
- **Delivery:** **inline SVG**, `width/height: 16` (20 for the empty-state, 14 for dense inline chips), `fill="none"`, `stroke="currentColor"`, `stroke-width="2"`, `stroke-linecap="round"`, `stroke-linejoin="round"`. Inline (not a CDN, not an icon font) so it works offline in WebView2 and inherits `currentColor` from the button's text color automatically.
- **Recommended mechanism:** a single inline **SVG sprite** (`<svg style="display:none"><symbol id="i-x" viewBox="0 0 24 24">…</symbol>…</svg>`) placed once at the top of `<body>`, referenced with `<svg class="icon"><use href="#i-x"/></svg>`. One definition per glyph, reused everywhere, still `currentColor`. (Individual inline SVGs are fine too but duplicate markup; avoid an icon font — it breaks `currentColor` sub-pixel and needs a bundled font file.)
- **Base class:**
  ```css
  .icon { width: 16px; height: 16px; flex: none; stroke: currentColor;
          fill: none; stroke-width: 2; vertical-align: -2px; }
  ```
- **Never** re-introduce emoji or box-drawing glyphs (◧ ◨ ⠿ ▸ ⟲ ↶ ↷ ✕) as icons.

**Icon map — every current glyph → Feather:**

| UI element | Current glyph | Feather icon | id |
|---|---|---|---|
| Export button | `▸` | `download` | `i-download` |
| Add files / pick files | (text) `📁` browse | `folder-plus` / `folder` | `i-folder-plus` |
| Browse folder (modal) | `📁` | `folder` | `i-folder` |
| Help / shortcuts | `?` | `help-circle` | `i-help` |
| Close modal | `✕` | `x` | `i-x` |
| Mark IN | `◧` | `log-in` (or `scissors`) | `i-mark-in` |
| Mark OUT | `◨` | `log-out` | `i-mark-out` |
| Delete cut | `✕` | `trash-2` | `i-trash` |
| Cancel mark | (text) | `x-circle` | `i-x-circle` |
| Undo | `↶` | `corner-up-left` | `i-undo` |
| Redo | `↷` | `corner-up-right` | `i-redo` |
| Tile drag grip | `⠿` | `more-vertical` | `i-grip` |
| Tile move left | `◀` | `chevron-left` | `i-chev-left` |
| Tile move right | `▶` | `chevron-right` | `i-chev-right` |
| Tile remove | `✕` | `x` | `i-x` |
| Zoom hint | `⟲` | `search` (or `zoom-in`) | `i-zoom` |
| Disclosure caret | `▸ / ▾` | `chevron-right` (rotates) | `i-chev-right` |
| Play (empty poster) | — | `play` | `i-play` |
| Skip-cuts toggle | (checkbox) | `scissors` label icon | `i-scissors` |
| Quality (modal) | — | `sliders` | `i-sliders` |
| Resolution (modal) | — | `monitor` | `i-monitor` |
| Filename (modal) | — | `file` | `i-file` |
| Keep audio | — | `volume-2` | `i-volume` |
| Open output folder (toast) | — | `folder` / `external-link` | `i-external` |
| ffmpeg ok / bad | — | `check-circle` / `alert-triangle` | `i-check` / `i-alert` |

---

## 8. Component conventions

### Buttons
Four variants. All: `--r-sm`, `--fs-md`, weight 500, `padding: 6px 12px`, `:active { transform: scale(0.97) }`, 16px leading icon where it clarifies.

- **Primary** (`.primary`) — one per context (Export, modal confirm). `background: var(--accent); color: var(--accent-ink); font-weight:600; border:none;` hover `--accent-hi`. Larger: `padding: 8px 16px`.
- **Secondary** (default `button`) — `background: var(--fill-soft); color: var(--ink-1); border: none;` hover `background: var(--fill-med)`. (Replaces the current solid `--bg-4` + hard border.)
- **Ghost** (`.ghost`) — `background: transparent; color: var(--ink-2); border:none;` hover `background: var(--fill-soft); color: var(--ink-1)`. For Undo/Redo/Cancel.
- **Danger** (`#btnDeleteCut`, tile remove) — ghost-danger at rest: `background: transparent; color: var(--danger); border: 1px solid var(--danger-dim);` hover `background: var(--danger); color:#fff; border-color:transparent`.
- **Icon button** (`.icon-btn`) — `--r-full`, `28px` square, `color: var(--ink-3); background: var(--fill-soft);` hover `--ink-1 / --fill-med`. Weight normal (icon carries it).
- **Disabled** — `opacity: 0.4; cursor: not-allowed;` no color change.
- **Mark IN/OUT accent:** drop the colored left-border hack. Use a leading icon (`log-in`/`log-out`) in `--ink-2`; when a mark is pending, Mark OUT gets `box-shadow: 0 0 0 1px var(--mark)` + subtle pulse and Mark IN drops to `opacity:.5`.

### Inputs & selects
`background: var(--surface-3); color: var(--ink-1); border: 1px solid var(--hairline); border-radius: var(--r-sm); padding: 7px 10px; font-size: var(--fs-md);` focus: `border-color: transparent; box-shadow: 0 0 0 1px var(--accent), 0 0 0 3px var(--accent-ring);`. Placeholder `--ink-4`. Path/timecode inputs keep `--mono`.

### Modals
Backdrop `rgba(8,9,10,0.6)` + `backdrop-filter: blur(2px)`. Panel: `background: var(--surface-1); border: 1px solid var(--hairline-str); border-radius: var(--r-lg); box-shadow: var(--shadow-3);`. Head/foot separated from body by surface tone or a single `--hairline`, not both. Title `--fs-lg` weight 600. Enter: backdrop fade + panel `opacity 0→1, scale .97→1` over `--dur-slow ease-out`.

### Toasts / snackbars
`background: var(--surface-2); border: 1px solid var(--hairline); border-radius: var(--r-md); box-shadow: var(--shadow-2);`. Status variants tint via `--good/--warn/--danger` on a **left 2px bar or the icon only** — keep the body monochrome so status pops. Enter `translateY(6px)+opacity` over `--dur-mid`.

### Progress bars
Track `var(--fill-soft)`, fill `var(--accent)`; ok state `--good`, error `--danger`. No glow shadow (drop the current `box-shadow` on timeline progress — reads cheap; a crisp solid fill reads premium).

### Timeline (the signature surface — preserve all R1–3 behavior)
- **Track:** `background: var(--well); border: 1px solid var(--hairline); border-radius: var(--r-md);` hover `border-color: var(--hairline-str)`. Drop the top-gradient sheen; keep it flat.
- **Ruler ticks:** `rgba(255,255,255,0.14)`; labels `--ink-3`, `--fs-micro`, tabular-nums.
- **Progress fill:** `--accent`, 3px, no glow.
- **Playhead:** 2px `--ink-1` (white), no glow; the time bubble uses `--surface-2` + `--hairline-str` + `--r-sm`.
- **Cut band:** replace the harsh candy-stripe with a calmer destructive region — flat `background: var(--cut)` with a 1px `--cut-hi` left/right edge; on hover `background: rgba(229,103,78,0.55)`; selected `box-shadow: var(--ring), 0 0 0 2px var(--cut-hi)`. Optional very-low-contrast diagonal texture at ≤0.06 alpha if a fill-only band reads too plain.
- **Handles:** the white grab bars stay (functional); soften to `--r-full` inner bar.
- **Pending mark:** 2px `--mark`, subtle glow acceptable (it is transient in-progress state).
- **Hover thumb-preview:** `--surface-1`, `--hairline-str`, `--r-md`, `--shadow-2`.

### Clip tiles
`background: var(--surface-1); border: 1px solid var(--hairline); border-radius: var(--r-md);` (drop the current 2px border — too heavy). Active: `border-color: var(--accent)` + inset accent bar. Hover: `border-color: var(--hairline-str)` + optional `transform: translateY(-1px)`. Reorder arrows + grip reveal on hover (existing behavior — keep). Missing state tints via `--danger`.

### Chips / pills (zoom-info, cut-stats, ffmpeg-status)
`background: var(--fill-soft); border: none; border-radius: var(--r-full); padding: 2px 8px; font-size: var(--fs-xs); color: var(--ink-2);`. Status chips (ffmpeg) tint bg via `--good-dim`/`--danger-dim` and text via `--good`/`--danger`.

---

## 9. Progressive disclosure guidelines

Hide power until needed — **without hurting the discoverability R1–3 built.** Rule of thumb: a control that is only useful *after* a precondition, or that a first-timer never needs, can collapse behind a trigger that is itself always visible and labeled.

- **Always visible (never hidden):** the primary path — Add files, the timeline, Mark IN/OUT, Export, the shortcuts affordance. These are the discoverable core.
- **Reveal-on-context (already good, keep):** Delete-cut (only when a cut is selected), Cancel (only when a mark is pending), tile reorder arrows/grip (on hover/focus), the hover thumb-preview, the "zoomed" reset chip.
- **Collapse candidates:**
  - **Undo/Redo** → move off the always-visible cut-controls row into a compact icon pair (they have keyboard shortcuts and are secondary). They stay on screen but as quiet ghost icon-buttons, not full labeled buttons — reduces the 6-button row to the 2 marks + contextual delete.
  - **Speed + Skip-cuts** → these are "playback settings"; on a narrow window they can collapse into a single `settings`/`sliders` popover on the timeline tools row. Keep them inline when space allows.
  - **Export advanced options** (Quality, Resolution, Accurate cuts, Keep audio) → group Resolution + the two checkboxes under a **"More options"** `<details>` disclosure inside the modal, defaulting closed. Folder + Filename + Quality stay visible (the 90% path). This trims the modal from 6 controls to 3 by default.
  - **"By path"** → already a `<details>` disclosure. Keep, restyle the caret to a rotating Feather chevron.
- **Trigger conventions:** a disclosure trigger is always a labeled control (text + rotating `chevron-right`), never a bare icon with no label. Disclosure animates with `--dur-mid ease-out` (height via `grid-template-rows` or opacity/transform, not animating `height` directly).

---

## 10. Implementation order

1. Retheme `:root` (surfaces, ink-by-opacity, accent `#5e6ad2`, spacing, radii, motion) — global lift.
2. Inline the Feather SVG sprite; replace every emoji/glyph with `<use>`.
3. Refit buttons/inputs/modals/toasts to the fill-wash + hairline conventions.
4. Retheme the timeline (flat track, calmer cut band, no glows).
5. Apply the progressive-disclosure moves.
6. Type pass (Inter/@font-face if bundled, weights 400/500/600, tabular-nums, tracking).
7. Anti-slop review — confirm: one accent, no emoji, no border+shadow stacking, one spacing scale, focal point per screen.
