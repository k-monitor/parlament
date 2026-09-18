# Parlamonitor — brand & styling kit

This is a **brand/styling kit**, not a component library. It carries Parlamonitor's
design tokens (colors, typography, layout, faction palette) and the app's utility-class
vocabulary, extracted verbatim from the shipping product. There are no bound components —
build your own React components and **style them with the tokens and classes below** so
every design matches Parlamonitor.

Parlamonitor is a civic-tech tool (K-Monitor) for searching the Hungarian Parliament's
transcripts. The look is calm, document-like, and text-first: warm paper-white surfaces,
one soft shadow, one corner radius, and a single strong brand red used sparingly.

## Setup

No provider, theme wrapper, or build step is required. It is plain CSS: all tokens live on
`:root` and all classes are global. Load one file — `styles.css` — and everything (tokens +
utilities) is available. `styles.css` `@import`s the four token files under `tokens/`, so
you never link those directly.

```html
<link rel="stylesheet" href="styles.css" />
```

## Styling idiom

**Two tools: CSS custom properties (`var(--*)`) for values, and utility classes for the
handful of recurring UI patterns.** There is no CSS framework and no class-per-property
system — write your own layout CSS, but pull every color, radius, shadow, and font from the
tokens, and reach for a utility class when one fits.

### Design tokens (use these, never hardcode hex)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f7f6f3` | page background (warm off-white) |
| `--surface` | `#ffffff` | cards, inputs, raised surfaces |
| `--ink` | `#3e3b32` | primary text (>9:1 on surface) |
| `--ink-soft` | `#56524a` | secondary text (~7:1, AA) |
| `--ink-faint` | `#6f6a60` | muted / tertiary text (~5:1, AA large) |
| `--line` | `#e2e0da` | borders, dividers |
| `--accent` | `#B22817` | brand red — primary actions, links, emphasis |
| `--accent-ink` | `#ffffff` | text on `--accent` |
| `--accent-soft` | `#f8e7e4` | tinted accent background / hover fill |
| `--accent-strong` | `#8e2012` | accent hover / active |
| `--focus` | `#0D5F94` | brand blue — keyboard focus rings only |
| `--mark` / `--mark-ink` | `#fff3bf` / `#5c4400` | search-highlight bg / text |
| `--section-band` | `#e9e3d5` | section masthead band (Elemzések) |
| `--section-bg` | `#f3efe5` | page ground inside that section |
| `--section-line` | `#d9d2c1` | the band's bottom hairline |
| `--radius` | `10px` | corner radius (cards, buttons) |
| `--maxw` | `1100px` | max content width |
| `--gutter` | `1.5rem` | page side-margin |
| `--shadow` | soft two-layer | the one card elevation |
| `--font` | system sans stack | primary UI font |
| `--font-serif` | Georgia stack | Wikipedia "W" entity badges |
| `--font-mono` | ui-monospace stack | embed codes / snippets |
| `--line-height` | `1.55` | base body rhythm |

Faction (party) colors are a **categorical palette** in `tokens/faction-palette.css`:
`--faction-fidesz` `#FF6A13`, `--faction-kdnp` `#0B4C8C`, `--faction-tisza` `#00A6A6`,
`--faction-dk` `#1E5BC6`, `--faction-jobbik` `#5A3B1C`, `--faction-mszp` `#C8102E`,
`--faction-momentum` `#8E44AD`, `--faction-lmp` `#3DA639`, `--faction-parbeszed` `#1B9E77`,
`--faction-mihazank` `#2E5E2E`, `--faction-independent` `#888888`. For other/unknown series
cycle `--cat-1` … `--cat-10`. Color is always decorative here — the label must carry the
meaning without it (accessibility).

The **section grounds** mark a change of part rather than a change of component: inside the Elemzések (analyses) section the page background becomes `--section-bg`, under a masthead band of `--section-band` closed by a `--section-line` hairline. Apply them to the page and band backgrounds directly — there is no class for this; the app toggles it per route.

### Utility classes

- **Layout**: `.container` (centered, max `--maxw`, `--gutter` sides) · `.page` (page vertical padding) · `.grid` (gap grid) · `.row` (flex row, wraps, centered) · `.center`
- **Type**: `.muted` (faint) · `.soft` (soft ink) · `.small` (.85rem). Headings `h1`/`h2`/`h3` are pre-scaled.
- **Cards**: `.card` (surface + line + `--shadow` + `--radius`) · `.pad` (inner padding). Combine: `<div class="card pad">`.
- **Entry cards**: `.explore-grid` (1/2/3-column responsive track) holding `.card.feature` tiles — each `.feature` is an icon (`.feature__icon`), an `h2`, a sentence and an arrow (`.feature__cta`); hovering lifts the card and fills the icon with `--accent`.
- **Figure footer**: `.fig-foot` — the bottom row of a chart card, for an embed/share control flush right.
- **Buttons**: `.btn` (solid red primary) · `.btn.secondary` (surface + line outline) · disabled via `disabled`.
- **Inputs**: bare `input[type=text|search|date]` and `select` are styled; pair with a `<label>`.
- **Chips & badges**: `.chip` with a `.dot` (faction color swatch) · `.chip-link` (clickable chip) · `.badge`, `.badge.warn` (pill labels).
- **Search UI**: `.filters`, `.filter-grid`, `.results-head`, `.sortctl`; highlight matches with `<mark>`.
- **States**: `.state` (empty/loading block) · `.spinner` · `.visually-hidden` (SR-only).
- **Tables**: `table.data` (bordered, compact data table).
- **Avatars**: `.avatar` + `.lg` / `.sm` / `.xs`.

## Where the truth lives

Read these before styling — they are authoritative and small:

- `styles.css` — base element styles + every utility class above.
- `tokens/colors.css`, `tokens/typography.css`, `tokens/layout.css`, `tokens/faction-palette.css` — the tokens, with inline notes.

## Idiomatic snippet

```jsx
// A result card — library-free; style is entirely tokens + utility classes.
function SpeechCard({ speaker, faction, snippet }) {
  return (
    <div className="card pad" style={{ maxWidth: "var(--maxw)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{speaker}</strong>
        <span className="chip">
          <span className="dot" style={{ background: "var(--faction-fidesz)" }} />
          {faction}
        </span>
      </div>
      <p className="soft" style={{ marginTop: ".4rem" }}>{snippet}</p>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: ".8rem" }}>
        <button className="btn secondary">Részletek</button>
        <button className="btn">Megnyitás</button>
      </div>
    </div>
  );
}
```

> Source: extracted from the Parlamonitor Vue 3 SPA (`frontend/src/styles.css`). The tokens
> and classes mirror the shipping product; there are no bound components in this kit.
