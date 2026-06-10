# Diagram & visual-asset system

Version-controlled **SVG** for diagrams (small, crisp, diff-able). WebP/PNG only for
screenshots. **No essential information lives only in an image** — every embedded diagram
is paired with a text equivalent (a caption or list) so the docs work with images off,
on a screen reader, or on GitHub where SVGs don't theme.

## Files

| File | Diagram | Used in |
|---|---|---|
| `flow.svg` | What happens: agent → hook → character → channels | home, README |
| `escalation-ladder.svg` | Jan → Bazza → Karren by severity | home, characters guide |
| `install-profiles.svg` | minimal · default · full (disk/RAM) | install profiles |
| `model-lifecycle.svg` | download → on-demand → idle-unload → persistent | minimum specs |
| `privacy-boundary.svg` | local-only, loopback services, opt-in | install profiles (MCP/network) |
| `storage-map.svg` | macOS Application Support / Caches / Logs | configuration |

## Palette

Tokens are chosen to read on **both** light and dark backgrounds: every shape has its own
light-tinted fill with dark text, so a diagram is legible whatever the page behind it (the
Material light/dark toggle and GitHub's themes both work without per-theme image swaps).

| Role | Fill | Border | Text |
|---|---|---|---|
| Neutral stage | `#eef1fb` | `#5b5bd6` | `#1f2333` |
| Accent | — | `#d6409f` | — |
| **Jan** (routine / done) | `#e7f5ec` | `#2f9e57` | `#1b5e34` |
| **Bazza** (warning / attention) | `#fdf3e3` | `#d98a1f` | `#8a5510` |
| **Karren** (error / urgent) | `#fdebec` | `#d64550` | `#8c2a31` |
| Connector / arrow | — | `#8a8fa3` | — |

## Type

System sans, set in-SVG:
`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`.
Body text ≥ 14px in the viewBox (legible when scaled down on mobile); headings bold.

## Conventions

- **Colour is never the only signal** — characters and statuses always carry a name/label
  and an icon, not just a hue (accessibility, colour-blind safe).
- Each SVG includes `<title>` and `<desc>` for assistive tech.
- Keep the canvas transparent; put all text on a filled shape so nothing depends on the
  page background.
- Prefer vertical layouts — they scale better on phones.

## Adding a diagram

1. Draw it as SVG here using the palette/type above.
2. Embed with an `<img>` + `alt`, then add a text equivalent (caption or list) below it.
3. `mkdocs build --strict` must stay clean.
