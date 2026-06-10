# Demo & media assets

Drop launch/demo media here.

## Screen capture (inline)

- **`demo.gif`** — a 15–20 s screen capture of a vibe firing (e.g. Karren on an error):
  hold music → the character picks up → haptic/display fire. Referenced from the top-level
  [`README.md`](https://github.com/guruswami-ai/not-happy-jan/blob/main/README.md) (an
  HTML-commented `<img>` is ready to uncomment).
- Screenshots, the AWTRIX tiles, etc.

Keep clips short and compressed — they ship in the repo. A few seconds of the *experience*
sells this faster than any prose.

**`awtrix-demo.webp`** — the AWTRIX/Ulanzi matrix reacting to a vibe (used in the README and
[Watch the demo](../watch-the-demo.md)). Animated **WebP**, lossless, 526×130 — ~56 KB.
WebP renders inline on GitHub *and* the docs site; prefer it over GIF for animated captures
(this clip was 1.5 MB as a raw GIF). Recipe to re-optimise a capture — half-size,
nearest-neighbour to keep pixels crisp, 12 fps, lossless WebP:

```sh
ffmpeg -i capture.gif -vf "fps=12,scale=iw/2:ih/2:flags=neighbor,palettegen=max_colors=8:stats_mode=full" pal.png
ffmpeg -i capture.gif -i pal.png -lavfi "fps=12,scale=iw/2:ih/2:flags=neighbor[x];[x][1:v]paletteuse=dither=none" out.gif
gif2webp -m 6 out.gif -o out.webp     # lossless animated WebP — tiny for flat pixel art
```

## Video thumbnails (YouTube embeds)

For the longer demos hosted on YouTube (see **[Watch the demo](../watch-the-demo.md)** and
its transcripts), drop a clickable thumbnail here under `video/`:

- **`video/demo-thumbnail.webp`** — the overview-video poster. ~1280×720, WebP, compressed.
- One per video, named to match (`video/setup-thumbnail.webp`, …).

The thumbnail is the linked fallback shown beside every embed (no autoplay, privacy-enhanced
`youtube-nocookie` embed). Until the real asset lands, the README/watch-page reference stays
HTML-commented so nothing renders broken. Full per-video requirements (captions, transcript,
chapters) live on the **[Watch the demo](../watch-the-demo.md)** page.
