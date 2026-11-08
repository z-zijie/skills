# Design language

The visual and motion rules for this dashboard. `web/css/tokens.css` implements the tokens below; this file is their authority. Checked-in values are contrast-verified (WCAG 2.x: 4.5:1 for text under 18.66px, 3:1 for large text, data marks, and focus rings).

The register: warm paper, near-black ink, one terracotta accent, hairline rules, dense information at low volume. Bright and precise, never decorative. Everything in this file exists to keep a full board of cards and their charts readable at a glance.

## Sources

Reference designs and techniques, with repository stars read 2026-11-09:

- [anthropics/skills](https://github.com/anthropics/skills) — 189,357★. `skills/brand-guidelines` (official Anthropic palette), `skills/frontend-design` (anti-slop checklist).
- [abdiraimov-otabek/dashboard-design-skill](https://github.com/abdiraimov-otabek/dashboard-design-skill) — 413★. Perception and layout rules distilled from 127 shipped dashboards.
- [larashero3-dotcom/lieflat-charts](https://github.com/larashero3-dotcom/lieflat-charts) — 6,766★. Chart selection and SVG-only chart craft.
- [LottieFiles/motion-design-skill](https://github.com/LottieFiles/motion-design-skill) — 2,874★. Duration/easing tables and the motion quality checklist.
- [whaleyxbt/claude-motion](https://github.com/whaleyxbt/claude-motion) — 904★. Claude-styled motion curves, spring and exit rules.

Palette provenance is double-checked against the installed Claude Code 2.1.273 binary, which embeds `#FAF9F5`, `#141413`, `#D97757`, and the `ui-serif` display stack. Dark mode is deliberately out of scope (the product is a bright room; a half-built dark theme reads as unfinished).

## Palette

```css
--bg:#FAF9F5;            /* warm paper */
--bg-subtle:#F4F1EA;
--surface:#FFFFFF;
--surface-raised:#FFFDF9;
--line:#E8E6DC;          /* hairline */
--line-strong:#D6CFC2;
--ink:#141413;           /* Anthropic dark */
--ink-2:#5C574C;
--ink-3:#6E6A5F;
--ink-faint:#8A8478;     /* non-text only: axes, gridlines, muted marks, disabled */
--accent:#D97757;        /* fills, washes, numerals ≥19px, dots paired with a word */
--accent-text:#A84E30;   /* small text and the 2px focus ring */
--accent-ink:#B85C3D;    /* hover/active of accent-text */
--ok:#4B7456; --warn:#8E6320; --danger:#B84B37; --info:#5F6E79;
--chart-1:#C4694B; --chart-2:#9E7F3C; --chart-3:#788C5D; --chart-4:#5C7F9B; --chart-5:#7A7466;
```

The build also derives semantic washes (`--accent-wash`, `--ok-wash`, `--warn-wash`, `--danger-wash`, `--info-wash`), a lighter `--line-soft` for in-component dividers, and a four-step terracotta `--ramp-1..4` for composition; those are tints of the values above, not new hues.

Contrast on `--bg` / `--surface`: `--ink` 17.5 / 18.4, `--ink-2` 6.8 / 7.2, `--ink-3` 5.1 / 5.4, `--accent-text` 5.25, `--ok` 5.07, `--warn` 5.04, `--danger` 4.86, `--info` 4.99, `--chart-1..5` 3.49–4.41. `--accent` is 2.96 against paper: below the text bar, so it never carries small text on its own; `--ink` (#141413) on `--accent` is 5.9, so a filled terracotta chip may use ink text, while surface-coloured text on it (2.96) is large-text only.

Status colour is always paired with a word. A session is a dot plus its status word (`running`, `waiting`, `done`, `failed`). A machine is a dot plus `online`, or — when offline — a dashed hairline outline plus `offline`. Never colour alone.

## Typography

```css
--font-display: ui-serif, Charter, "Iowan Old Style", Georgia, "Times New Roman", serif;
--font-ui: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, "SF Mono", monospace;
```

Offline-safe: every face is a system font on macOS and Linux; no webfonts, no CDN.

- Serif is for display **words** only: the `dashboard` wordmark, the inspector title, and the empty-state title — weight 400, 19–21px, `line-height 1.15–1.18`, `letter-spacing -.02em`, `text-wrap: balance`. Body text and numbers stay in `--font-ui`.
- Numerals always use `--font-ui` with `font-variant-numeric: tabular-nums lining-nums`. Georgia and Iowan Old Style ship old-style figures, so a serif metrics row would have ragged digit heights.
- Mono only for session ids and timestamps: 9.5px. Labels use `--font-ui`.
- Caps labels: 10px (`--text-caps`), `letter-spacing .08em`, uppercase, `--ink-3`, for one- or two-word system nouns (`RUNNING`, `CLAUDE CODE`). Dense micro-caps inside cards, rail rows and the topology tooltip use the 9.5px micro step with tight .01–.075em tracking. They are labels, not sentences.
- Scale: 9.5 micro (stat keys, units, ids, timestamps) / 10 caps / 12–15 data values / 13 body / 15 titles / 19–21 hero numbers and display words. No rendered text below 9.5px.

## Surfaces, lines, radii

- Flat surfaces only: paper `--bg`, panels `--surface` / `--surface-raised`. No gradients or translucency on surfaces (only SVG chart fills use a soft wash gradient). No glass layers, no nested bordered cards.
- Hairlines: 1px `--line` (1.19:1 — visible but not structural) separates structure (header, rail, toolbar, footer, inspector sections); `--line-soft` separates in-card stat cells, inspector metric columns and small totals; `--line-strong` marks hover borders, the offline dashed outline, and scrollbar thumbs.
- Cards: 1px border, radius 10px, resting shadow `0 1px 0 rgba(20,20,19,.03)`; hover raises it to that plus `0 8px 24px rgba(20,20,19,.06)`.
- Radius scale: 10px cards · 12px inspector sheet · 6–8px controls · 999px chips.
- Hover shadow moves through a pre-rendered `::after` layer's opacity, never a tweened `box-shadow` on every card.

## Layout

1440×900 is the target; no horizontal scroll at 1440 / 1180 / 900 / 768.

```
header 64: wordmark + live/demo badge / machines, agents, sessions, requests / throughput sparkline + number + t/s / Board or Topology
rail 236: machine rows — state dot + name + "online" word, agent & session counts, TPS, thin meter; hover and selection use a wash, no dividers
toolbar 53: status chips (counts) / agent chips / search / sort
grid: repeat(auto-fill, minmax(268px,1fr)), gap 14, padding 18px 22px
inspector 408: right overlay drawer
footer 28: read-only / data mode / poll cadence / showing N of M sessions / updated Ns ago
```

- Board is the default view; Topology is a second view, not a second page.
- ≤1179px the machine rail becomes a horizontal chip strip; ≤1099px the header wraps to two rows; ≤899px the grid is single column and the inspector becomes a full-screen sheet.
- The inspector separates sections with hairlines and caps labels, never with bordered boxes.
- Space carries grouping before lines do; a card's rhythm is 4/6/8/12/14 (title→visual gap 8, paddings 12–14), an inspector section is padded 14/16 vertically and 22 horizontally with a 9px gap under its caps title.

## Motion

```css
--ease-out: cubic-bezier(.16,1,.3,1);      /* entrances, chart draw */
--ease-in: cubic-bezier(.6,0,.9,.35);      /* exits, chip removal */
--ease-state: cubic-bezier(.65,0,.35,1);   /* FLIP reorder, Board ↔ Topology */
--ease-panel: cubic-bezier(.2,.8,.2,1);    /* inspector drawer */
/* linear only for particles, progress rings, rotation */
```

| Job | Duration |
|---|---|
| hover in | 90ms |
| hover out | 160ms |
| press → release | 80ms → 150ms, `scale(.98)`, no overshoot |
| drawer open / close | 280ms / 200ms |
| FLIP reorder | 320ms |
| chart draw | 600ms |
| card stagger | 32ms each (a full 34-session board cascades ~1.5s; reduced motion skips it) |
| ambient loops (status dot, sparkline end point, topology particles) | 2600ms (2.6–3.2s loops) |

Exits are 65–75% of their entrance; hard cuts read as loading.

- Entrance choreography, each group after the one before: header (22ms stagger, `translateY(6px)`, 300ms) → rail rows (28ms stagger, 8px, 300ms) → toolbar (260ms fade) → cards (32ms stagger, 320ms, `translateY(8px)` + opacity) → chart line draw. Nothing spins or bounces.
- View changes keep persistent objects: the card's title/meta block becomes the inspector header; a session's dot persists into topology. Filtering transforms existing nodes instead of rebuilding them.
- Live updates tween numbers (~420ms) and slide the appending waveform segment; never blank, skeleton, or re-sort mid-update. When a poll fails, the board, rail and metrics dim to opacity .55 and the status line turns warn-coloured — no skeleton, no flash.
- Animate `transform` / `opacity` only, plus SVG `d` / `stroke-dashoffset`. One rAF clock; appends follow the data tick (1 Hz in demo, the snapshot refresh interval in live) and slide only the new segment and end dot.
- `prefers-reduced-motion: reduce` and `document.hidden` stop particles, pulses, and staggers; transitions collapse to ~0ms and every function remains.

## Charts and numbers

- Encoding ladder: position > length > colour intensity > area/angle. Order matters more than hue.
- Sparkline: no axes, no gridlines, no labels; one 60s window shared by every sparkline, ending on the last output-producing second; a last-point dot and a 16%→0 area wash. The stroke is state-tinted: `running` = `--chart-1`, `waiting`/`idle` = `--ink-faint`, `done` = `--chart-5`, `failed` = `--danger`. Only the running end dot pulses (ambient loop); an empty series draws a faint dashed line with no end dot. This value is exactly the card's displayed TPS.
- Context ring is a **capacity gauge** against the model limit (3px arc, `--ink-2` under 70%, `--warn` 70–85%, `--danger` above), never a part-to-whole pie. An unknown model window draws a dashed `--line-strong` ring labelled `n/a` instead of a percentage.
- Context composition: a horizontal stacked bar plus a legend of tokens and percentages, shown only when the collector-provided `context_parts` have at least two non-zero parts and sum to `context.used` within 2% (or two tokens). In that case the section is titled "Context composition"; otherwise it is titled "Context usage" and shows one capacity segment ("used X of Y" or "window unknown"). It never derives a composition from cumulative session tokens. Segment colours come from the four-step terracotta ramp.
- Throughput chart: area chart over the same 60s series the card sparkline uses, hover hit-area across the full plot height, crosshair plus a value tooltip.
- Run trace: waterfall rows coloured by kind (`think`/`tool`/`message`/`subagent`), max 8 rows plus a `+n more steps` row; hover reveals duration.
- Every number needs a baseline ("compared to what?"): previous window, limit, or a stated normal range. One number, one source, one clock: header aggregate, card sparkline and inspector chart all read the same snapshot/clock, and a card's TPS is exactly the last bucket of its sparkline, or the two will be screenshotted disagreeing.
- Formatting: runtime `1m 42s`, tokens `1.4M`, context `43%`, TPS to one decimal, cost `$2.41`. Tabular numerals everywhere a value can change.

## What not to do

From `frontend-design`'s "AI tell" list, adapted to this brief: no `A · B · C` middle-dot metadata strings (use `3 machines / 9 sessions`), no ALL-CAPS eyebrow above every heading, no `WORD — fragment` labels, no monospace on every small label, no `→` appended to button text, no purple/blue gradients, no neon, no glassmorphism, no heavy shadows, no oversized radii, no cards inside cards, no icon piles inside cards, no decorative animation that runs forever at full strength (≤1/3 of elements animating at once, none moving more than 1/3 of its container).

## Accessibility and performance

- Text meets AA against its actual surface; data marks and focus rings meet 3:1. `--accent-text` 2px `focus-visible` ring, never bare `--accent`.
- Cards and topology nodes are keyboard reachable: Enter opens, Esc closes, focus returns to the originating card; the inspector traps focus while open.
- Status is never colour alone; charts carry `aria-label` descriptions; `aria-live` announces only low-frequency summaries (never per-second TPS).
- First interactive paint under 1.5s locally with no external requests and no console errors or 404s. Keep the board DOM lean: one card template is reused and updated in place (the frozen build was reviewed on a 34-session snapshot), with chart paths rebuilt once per data tick rather than per frame.
