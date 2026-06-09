#!/usr/bin/env python3
"""Build a self-contained HTML review page for generated SFX candidates.

Scans a candidates directory (category subfolders of *_raw.wav / *_phone.wav clips
produced by generate-sfx.py) and emits review.html: per-clip audio players with
"keep" checkboxes and an Export button that downloads the approved file list as
approved.json. Open it in a browser on the machine that holds the audio files.

    python build-sfx-review.py --candidates audio/sfx/_candidates
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, default=Path("audio/sfx/_candidates"))
    args = ap.parse_args()
    root = args.candidates

    prompts: dict[str, str] = {}
    man = root / "MANIFEST.tsv"
    if man.exists():
        for line in man.read_text().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 3:
                cat = parts[0].split("/")[0]
                prompts.setdefault(cat, parts[2])

    cats = sorted(d for d in root.iterdir() if d.is_dir())
    rows = []
    total = 0
    for cat in cats:
        raws = sorted(cat.glob("*_raw.wav"))
        rows.append(f'<h2>{html.escape(cat.name)} '
                    f'<small>{html.escape(prompts.get(cat.name, ""))}</small></h2>')
        rows.append('<div class="grid">')
        for raw in raws:
            stem = raw.name[:-8]  # strip _raw.wav
            phone = cat / f"{stem}_phone.wav"
            rel_raw = raw.relative_to(root).as_posix()
            rel_phone = phone.relative_to(root).as_posix()
            total += 1
            cell = [f'<div class="card"><div class="lbl">{html.escape(stem)}</div>']
            cell.append(
                f'<div class="v"><label><input type="checkbox" data-f="{html.escape(rel_raw)}"> raw</label>'
                f'<audio controls preload="none" src="{html.escape(rel_raw)}"></audio></div>'
            )
            if phone.exists():
                cell.append(
                    f'<div class="v"><label><input type="checkbox" data-f="{html.escape(rel_phone)}"> phone</label>'
                    f'<audio controls preload="none" src="{html.escape(rel_phone)}"></audio></div>'
                )
            cell.append("</div>")
            rows.append("".join(cell))
        rows.append("</div>")

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>NHJ SFX review</title>
<style>
 body{{font:14px/1.4 -apple-system,system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}}
 header{{position:sticky;top:0;background:#161922;padding:12px 20px;border-bottom:1px solid #2a2f3a;display:flex;gap:16px;align-items:center;z-index:5}}
 h1{{font-size:16px;margin:0}} h2{{margin:24px 20px 8px;font-size:15px;color:#9ad}} h2 small{{color:#778;font-weight:400}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;padding:0 20px}}
 .card{{background:#161922;border:1px solid #2a2f3a;border-radius:8px;padding:8px}}
 .lbl{{font-size:12px;color:#aab;margin-bottom:6px}}
 .v{{display:flex;flex-direction:column;gap:4px;margin-bottom:6px}}
 audio{{width:100%;height:32px}}
 label{{cursor:pointer}} input{{accent-color:#4ad}}
 button{{background:#4a7;color:#031;border:0;padding:8px 14px;border-radius:6px;font-weight:600;cursor:pointer}}
 #count{{color:#9ad}} pre{{white-space:pre-wrap;background:#0a0c10;margin:10px 20px;padding:10px;border-radius:6px;display:none}}
</style></head><body>
<header><h1>NHJ SFX review</h1><span id="count">0 selected</span>
 <button onclick="exp()">Export approved.json</button>
 <span style="color:#778">{total} variations · tick the clips to keep</span></header>
{''.join(rows)}
<pre id="out"></pre>
<script>
 const cbs=()=>[...document.querySelectorAll('input[type=checkbox]')];
 function upd(){{document.getElementById('count').textContent=cbs().filter(c=>c.checked).length+' selected';}}
 document.addEventListener('change',upd);
 function exp(){{
   const sel=cbs().filter(c=>c.checked).map(c=>c.dataset.f);
   const blob=new Blob([JSON.stringify(sel,null,2)],{{type:'application/json'}});
   const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='approved.json';a.click();
   const o=document.getElementById('out');o.style.display='block';o.textContent=JSON.stringify(sel,null,2);
 }}
</script></body></html>"""
    out = root / "review.html"
    out.write_text(page)
    print(f"wrote {out}  ({total} variations across {len(cats)} categories)")
    print(f"open it:  open {out}")


if __name__ == "__main__":
    main()
