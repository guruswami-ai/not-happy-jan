# Watch the demo

<p align="center">
  <video controls preload="metadata" width="720" playsinline
         poster="../assets/video/demo-thumbnail.webp" style="max-width:100%;height:auto;border-radius:8px">
    <source src="../assets/video/demo.mp4" type="video/mp4">
    <track kind="captions" srclang="en" label="English" src="../assets/video/demo.vtt" default>
    Your browser can't play this video — <a href="../assets/video/demo.mp4">download the MP4</a> instead.
  </video>
</p>
<p class="caption">Not-Happy-Jan in 47 seconds — the cast and the integrations, with sound. Captions included; full <a href="transcripts/overview.md">transcript</a>.</p>

…and the same vibes land on a Ulanzi pixel display:

![A Ulanzi AWTRIX LED matrix scrolling an in-character Not-Happy-Jan message in green pixels, with a tick icon — the visual channel reacting to a vibe.](assets/awtrix-demo.webp){ width="420" .center }

---

## More to come

Longer walkthroughs (a full setup demo) can be hosted on **YouTube** — the embed blocks below
are wired and ready, each just needs a video ID. They'd use the privacy-enhanced
`youtube-nocookie` player, never autoplay, and ship with a transcript.

## 60–90 second overview

*Claude starts a task → hold music → Jan answers "done" → a warning escalates to Bazza →
an error escalates to Karren → a device reacts → ends on the one-line install.*

<!-- READY TO PUBLISH: replace VIDEO_ID, drop docs/assets/video/demo-thumbnail.webp, then uncomment.
<p align="center">
  <a href="https://www.youtube.com/watch?v=VIDEO_ID">
    <img src="assets/video/demo-thumbnail.webp" alt="Watch: Not-Happy-Jan narrates a Claude Code session — hold music, Jan's all-done, Bazza's warning, Karren's error" width="640">
  </a>
</p>

<div class="video-embed">
  <iframe width="640" height="360"
    src="https://www.youtube-nocookie.com/embed/VIDEO_ID"
    title="Not-Happy-Jan — 90-second overview"
    frameborder="0" loading="lazy"
    allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowfullscreen></iframe>
</div>

▶ **[Watch on YouTube](https://www.youtube.com/watch?v=VIDEO_ID)** · 📝 **[Transcript](transcripts/overview.md)**
-->

## 5-minute setup walkthrough

*Requirements → one-line install → first `nhj test ok` → Claude Code hook behaviour →
default vs full vs minimal → the local/private architecture → uninstall.*

<!-- READY TO PUBLISH: replace VIDEO_ID, drop docs/assets/video/setup-thumbnail.webp, then uncomment.
<p align="center">
  <a href="https://www.youtube.com/watch?v=VIDEO_ID">
    <img src="assets/video/setup-thumbnail.webp" alt="Watch: installing Not-Happy-Jan end to end on an Apple-Silicon Mac" width="640">
  </a>
</p>

▶ **[Watch on YouTube](https://www.youtube.com/watch?v=VIDEO_ID)** · 📝 **[Transcript](transcripts/setup.md)**
-->

---

## Publishing checklist (per video)

When a recording is ready, before uncommenting its block:

- [ ] Upload to YouTube **unlisted or public**; copy the 11-character video ID.
- [ ] Add a **WebP thumbnail** at `docs/assets/video/<name>-thumbnail.webp` (~1280×720).
- [ ] Replace every `VIDEO_ID` in this page's block, then uncomment it.
- [ ] Fill the matching transcript page ([overview](transcripts/overview.md) · [setup](transcripts/setup.md)) — full, human-reviewed.
- [ ] **Human-review the captions** on YouTube — don't ship auto-captions as-is.
- [ ] Add **chapters** and a descriptive title/description on YouTube.
- [ ] Mix so **music doesn't obscure speech**; describe important visual-only events in the
      narration or transcript.
- [ ] Don't rely on colour alone to distinguish characters or statuses.

For the README, the same thumbnail-link pattern sits HTML-commented near the top — uncomment
it once `docs/assets/video/demo-thumbnail.webp` exists.
