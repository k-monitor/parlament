# design-sync notes — Parlamonitor Brand Kit

Shape is **`brand`** (off-script): a tokens/styling-only kit, no bound components.
The frontend is a Vue 3 SPA, and Claude Design consumes compiled *React* libraries —
so components are deliberately out of scope. See `config.json`. Do not re-attempt a
component import.

## Sources of truth

| Bundle file | Extracted from |
|---|---|
| `tokens/colors.css`, `layout.css` | `frontend/src/styles.css` `:root` |
| `tokens/typography.css` | `:root` (`--font`) + inline literals (see below) |
| `tokens/faction-palette.css` | `backend/app/loader.py` `FACTION_COLORS` + `_FALLBACK_PALETTE` |
| `styles.css` | `frontend/src/styles.css` (global classes, verbatim) |

**Tokens that are distillations, not copies** — they exist in the kit but not in the
app's `:root`, so a naive grep will call them missing. All four verified 2026-09-18:

- `--accent-strong: #8e2012` → the literal in `.btn:hover` (`frontend/src/styles.css`)
  and `.donate__amount.active:hover` (`DonateCard.vue`).
- `--font-serif: Georgia, …` → `EntityText.vue` `.entity-badge__w`, `RepProfileView.vue` `.link-badge--w`.
- `--font-mono: ui-monospace, …` → `EmbedButton.vue`.
- `--cat-1` … `--cat-10` → `_FALLBACK_PALETTE` in `loader.py`, in order.

## Re-sync procedure

There is no converter and no `_ds_sync.json` anchor, so the diff is done by hand.
These two checks are the whole gate — both must come back clean:

```sh
# 1. class vocabulary must be identical
diff <(grep -oE '^\.[a-zA-Z0-9_-]+' frontend/src/styles.css | sort -u) \
     <(grep -oE '^\.[a-zA-Z0-9_-]+' ds-bundle/styles.css | sort -u)

# 2. every source :root token must exist in the bundle
for t in $(sed -n '/^:root/,/^}/p' frontend/src/styles.css | grep -oE -- '--[a-z-]+'); do
  grep -q -- "$t:" ds-bundle/tokens/*.css || echo "MISSING: $t"
done
```

Then validate the README names nothing that does not resolve (every `--token` and
`.class` it mentions must be defined in the bundle CSS), and check `@import`s resolve
and braces balance. Keep `tokens/tokens.json` in step with `tokens/colors.css`.

Note `body.section-analyses` and the `.sectionhead*` classes are **not** shipped:
the first is toggled per-route by `App.vue`, the second is component-scoped there.
Only the `--section-*` tokens are shipped, documented for direct use.

## Log

- **2026-09-18 re-sync.** Sources had drifted since the 2026-07-23 build (commit
  `3052759` "create analysis subpage" + the landing-page work). Added: `--section-band`
  / `--section-bg` / `--section-line`, `.fig-foot`, `.explore-grid`, `.feature` +
  `.feature__icon` / `.feature__cta`, and `--line-height` to the README table.
  Bundle rebuilt to full parity; **not uploaded** — DesignSync had no authorization in
  that session (`/design-login` must be run once from an interactive session).
