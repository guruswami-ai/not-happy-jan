"""Not-Happy-Jan CLI — nhj <command>"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nhj import resources

app     = Console()
cli     = typer.Typer(help="Not-Happy-Jan — multi-sensory agent feedback")
_ROOT   = Path(__file__).resolve().parents[2]   # checkout root (for .venv detection / launchd cwd)
_VENV   = _ROOT / ".venv"
_PYTHON = _VENV / "bin" / "python" if (_VENV / "bin" / "python").exists() else Path(sys.executable)


@cli.command()
def test(
    intent: str = typer.Argument("ok", help="Intent: ok|err|warn|attn|celebrate|step"),
    message: str = typer.Option("", "--message", "-m"),
):
    """Fire a test vibe."""
    from nhj.queue_manager import QueueManager
    qm = QueueManager()
    qm.add(intent=intent, message=message)
    qm.start_worker_if_needed()
    app.print(f"[green]✓[/green] Queued [bold]{intent}[/bold]" + (f": {message}" if message else ""))


# NHJ's four Claude Code hooks. Stop = the [vibes:] voice/feedback; StopFailure =
# immediate idle mark on API errors; UserPromptSubmit + SessionEnd = the inference-muzak
# busy-set (ref-counted across sessions) + secret guard.
# Module names — run via `python -m`, so hooks work from a checkout OR a wheel
# install (no src/nhj/*.py path baked into settings.json).
_HOOK_WIRING = {
    "Stop":             "nhj.hook",
    "StopFailure":      "nhj.prompt_hook",
    "UserPromptSubmit": "nhj.prompt_hook",
    "SessionEnd":       "nhj.prompt_hook",
}
# Per-event metadata for exec-form hook entries.
# Stop timeout covers NHJ_HOOK_WAIT (default 3 s) plus dispatch overhead.
# UserPromptSubmit is kept short because it blocks every prompt submission.
# SessionEnd matches Claude Code's own short cleanup window.
_HOOK_META = {
    "Stop":             {"description": "NHJ: speak [vibes:] markers and pause inference muzak",
                         "timeout": 30},
    "StopFailure":      {"description": "NHJ: mark session idle after Claude Code API errors",
                         "timeout": 10},
    "UserPromptSubmit": {"description": "NHJ: start inference muzak and scan for leaked secrets",
                         "timeout": 10},
    "SessionEnd":       {"description": "NHJ: mark session idle for inference muzak",
                         "timeout": 10},
}
# Commands NHJ replaces when it installs (its own old entries + the legacy AgentVibes
# Stop hook — same [vibes:] markers, so leaving it in would double-fire).
# Match BOTH the new exec-form args (`-m nhj.`), the old shell-form command string, and
# the legacy src/ path form (`/…/nhj/hook.py`), so re-installing supersedes old entries
# instead of duplicating them.
_SUPERSEDED = ("not-happy-jan", "/nhj/", "-m nhj.", "haptic-wakey-wakey",
               "vibes_marker_hook", "agentvibes")


def _is_superseded(h: dict) -> bool:
    """Return True if a hook entry is an NHJ or legacy-AgentVibes entry."""
    cmd = h.get("command", "")
    args_text = " ".join(h.get("args") or [])
    full_text = f"{cmd} {args_text}"
    return any(s in full_text for s in _SUPERSEDED)


def _strip_superseded(blocks: list) -> list:
    """Remove NHJ/legacy-AgentVibes hook commands from an event's blocks."""
    for b in blocks:
        b["hooks"] = [h for h in b.get("hooks", []) if not _is_superseded(h)]
    return [b for b in blocks if b.get("hooks")]


@cli.command("install-hook")
def install_hook(
    settings: Path = typer.Option(
        Path.home() / ".claude" / "settings.json",
        help="Path to Claude Code settings.json",
    ),
):
    """Install NHJ's Claude Code hooks (Stop, StopFailure, UserPromptSubmit, SessionEnd).

    Supersedes the legacy AgentVibes / haptic-wakey-wakey Stop hook (shared [vibes:]
    markers). A timestamped backup of settings.json is written alongside it.
    """
    py = str(_PYTHON)
    cfg: dict = {}
    if settings.exists():
        raw = settings.read_text()
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            pass
        bak = settings.with_suffix(".json.pre-nhj.bak")
        if not bak.exists():
            bak.write_text(raw)
            app.print(f"[dim]backup → {bak.name}[/dim]")

    hooks = cfg.setdefault("hooks", {})
    for event, mod in _HOOK_WIRING.items():
        meta = _HOOK_META[event]
        hook_entry = {
            "type": "command",
            "command": py,
            "args": ["-m", mod],
            "description": meta["description"],
            "timeout": meta["timeout"],
        }
        blocks = _strip_superseded(hooks.setdefault(event, []))   # idempotent + drop legacy
        blocks.append({"matcher": "", "hooks": [hook_entry]})
        hooks[event] = blocks

    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(cfg, indent=2))
    app.print(f"[green]✓[/green] Installed NHJ hooks: {', '.join(_HOOK_WIRING)}")
    app.print(f"  [dim]→ {settings}  ·  verify with /hooks · restart Claude Code to activate[/dim]")


@cli.command("install-skill")
def install_skill(
    destination: Path = typer.Option(
        resources.claude_skill_install_path(),
        help="Destination path for the Claude Code skill file",
    ),
):
    """Install NHJ's Claude Code skill for non-checkout / wheel users."""
    source = resources.claude_skill_file()
    if not source.exists():
        app.print("[red]Bundled skill file not found. This may indicate a packaging issue.[/red]")
        raise typer.Exit(1)
    target = destination.expanduser()
    body = source.read_text()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text() == body:
        app.print(f"[green]✓[/green] NHJ skill already installed at [dim]{target}[/dim]")
        return
    if target.exists():
        backup = target.with_suffix(".md.pre-nhj.bak")
        backup.write_text(target.read_text())
        app.print(f"[dim]Backed up existing skill → {backup}[/dim]")
    target.write_text(body)
    app.print("[green]✓[/green] Installed NHJ Claude Code skill")
    app.print(f"  [dim]→ {target}[/dim]")


@cli.command("remove-hook")
def remove_hook(
    settings: Path = typer.Option(
        Path.home() / ".claude" / "settings.json",
        help="Path to Claude Code settings.json",
    ),
):
    """Remove NHJ's hooks from Claude Code settings (all three events)."""
    if not settings.exists():
        app.print("[yellow]settings.json not found[/yellow]")
        return
    cfg = json.loads(settings.read_text())
    hooks = cfg.get("hooks", {})
    for event in _HOOK_WIRING:
        if event in hooks:
            hooks[event] = _strip_superseded(hooks[event])
    settings.write_text(json.dumps(cfg, indent=2))
    app.print("[green]✓[/green] NHJ hooks removed. "
              "[dim](legacy AgentVibes not restored — see settings.json.pre-nhj.bak)[/dim]")


# The Stop hook only fires if Claude actually emits [Jan:ok|…] markers, so NHJ must
# also teach Claude to emit them. We do that by writing a managed block into the user's
# CLAUDE.md (always-on, idempotent, cleanly removable) — without it the hooks are inert.
_MARKERS_START = "<!-- not-happy-jan:start (managed by `nhj install-markers`) -->"
_MARKERS_END = "<!-- not-happy-jan:end -->"


def _markers_block() -> str:
    body = resources.bundled("hooks/markers.md").read_text().strip()
    return f"{_MARKERS_START}\n{body}\n{_MARKERS_END}"


@cli.command("install-markers")
def install_markers(
    claude_md: Path = typer.Option(
        Path.home() / ".claude" / "CLAUDE.md",
        help="Path to Claude's user CLAUDE.md",
    ),
):
    """Teach Claude to emit task-status markers so the Stop hook can speak them.

    Writes a managed block into CLAUDE.md (idempotent — re-running replaces it).
    Without this the hooks never fire from normal Claude activity.
    """
    import re
    block = _markers_block()
    claude_md = claude_md.expanduser()
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    existing = claude_md.read_text() if claude_md.exists() else ""
    if _MARKERS_START in existing and _MARKERS_END in existing:
        new = re.sub(re.escape(_MARKERS_START) + r".*?" + re.escape(_MARKERS_END),
                     lambda _m: block, existing, count=1, flags=re.S)
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new = (existing + "\n" if existing else "") + block + "\n"
        if claude_md.exists():                       # back up once before first append
            bak = claude_md.with_suffix(".md.pre-nhj.bak")
            if not bak.exists():
                bak.write_text(existing)
    claude_md.write_text(new)
    app.print(f"[green]✓[/green] Installed NHJ task-status markers → {claude_md}")
    app.print("  [dim]Claude will emit [Jan:ok|…] / [Karren:err|…] markers; the Stop hook speaks them.[/dim]")


@cli.command("remove-markers")
def remove_markers(
    claude_md: Path = typer.Option(
        Path.home() / ".claude" / "CLAUDE.md",
        help="Path to Claude's user CLAUDE.md",
    ),
):
    """Remove NHJ's managed marker block from CLAUDE.md."""
    import re
    claude_md = claude_md.expanduser()
    if not claude_md.exists():
        app.print("[yellow]CLAUDE.md not found[/yellow]")
        return
    text = claude_md.read_text()
    new = re.sub(r"\n*" + re.escape(_MARKERS_START) + r".*?" + re.escape(_MARKERS_END) + r"\n*",
                 "\n", text, flags=re.S)
    claude_md.write_text(new)
    app.print("[green]✓[/green] NHJ marker block removed from CLAUDE.md")


def _stop_nhj_services() -> list[str]:
    """Best-effort stop of NHJ's optional services so nothing is left running/orphaned:
    unload the TTS / LLM / MCP LaunchAgents, remove their plists, and kill any on-demand
    processes (worker, servers, muzak, the ocker-bogan-nano llama-server). macOS launchctl;
    a no-op on other platforms. Returns the plist filenames removed (for reporting)."""
    import shutil as _sh
    removed: list[str] = []
    la_dir = Path.home() / "Library" / "LaunchAgents"
    labels = (_TTS_LABEL, _OCKER_LABEL, _MCP_LABEL)
    if _sh.which("launchctl"):
        uid = os.getuid()
        for lbl in labels:                                    # unload running agents
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{lbl}"], capture_output=True)
    for lbl in labels:                                        # remove the plists
        for p in (la_dir / f"{lbl}.plist", la_dir / f"{lbl}.plist.disabled"):
            if p.exists():
                p.unlink(missing_ok=True)
                removed.append(p.name)
    if _sh.which("pkill"):                                    # kill stray on-demand procs
        for pat in ("nhj.tts_server", "nhj.mcp_server", "nhj.worker",
                    "nhj.inference_muzak", "ocker-bogan-nano"):
            subprocess.run(["pkill", "-f", pat], capture_output=True)
    return removed


@cli.command()
def uninstall(
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation prompt"),
):
    """Remove Not-Happy-Jan completely.

    Strips the Claude hooks/MCP/markers/skill, then deletes the runtime, config (.env),
    downloaded models + media, caches/logs, and the `nhj` launcher. Your Claude install
    itself is left untouched (only NHJ's own entries are removed).
    """
    import shutil
    home = Path.home()
    settings = home / ".claude" / "settings.json"
    claude_md = home / ".claude" / "CLAUDE.md"
    bin_link = Path(os.environ.get("NHJ_BIN_DIR") or home / ".local" / "bin") / "nhj"
    skill_dir = resources.claude_skill_install_path().expanduser().parent

    # All platform roots NHJ may have written (deduped): runtime/config/state/cache/logs/media.
    dirs: list[Path] = []
    for fn in (resources.app_support_dir, resources.data_dir, resources.config_dir,
               resources.state_dir, resources.cache_dir, resources.log_dir):
        try:
            p = fn(create=False)
        except Exception:
            continue
        if p and p not in dirs:
            dirs.append(p)
    # SAFETY: never delete a source checkout. In a dev checkout data_dir() resolves to
    # the repo root; skip anything that looks like a source tree (pyproject.toml / .git).
    existing = [p for p in dirs
                if p.exists() and not (p / "pyproject.toml").exists() and not (p / ".git").exists()]

    app.print("[bold]This will remove Not-Happy-Jan:[/bold]")
    for p in existing:
        app.print(f"  - {p}")
    if skill_dir.exists():
        app.print(f"  - {skill_dir}")
    if bin_link.is_symlink() or bin_link.exists():
        app.print(f"  - {bin_link}")
    app.print("  - stop + remove NHJ services (TTS / LLM / MCP LaunchAgents) and processes")
    app.print("  - NHJ hooks + MCP from settings.json, marker block from CLAUDE.md")
    if not yes:
        typer.confirm("Proceed?", abort=True)

    # 1. Stop the optional services FIRST so nothing keeps running or is left orphaned.
    stopped = _stop_nhj_services()
    if stopped:
        app.print(f"[dim]stopped services / removed LaunchAgents: {', '.join(stopped)}[/dim]")
    # 2. Claude integration (best-effort — files may already be gone)
    for fn, arg in ((remove_hook, settings), (remove_mcp, settings), (remove_markers, claude_md)):
        try:
            fn(arg)
        except Exception:
            pass
    # 3. skill + launcher
    if skill_dir.exists():
        shutil.rmtree(skill_dir, ignore_errors=True)
    if bin_link.is_symlink() or bin_link.exists():
        bin_link.unlink(missing_ok=True)
    # 4. runtime / config / caches / media (this removes the venv we're running from —
    #    fine: the interpreter is already loaded; the process finishes and exits).
    for p in existing:
        shutil.rmtree(p, ignore_errors=True)
    # 5. transient on-disk queue (best-effort)
    import glob as _glob
    for q in _glob.glob("/tmp/nhj-vibe-queue-*"):
        shutil.rmtree(q, ignore_errors=True)

    app.print("[green]✓[/green] Not-Happy-Jan uninstalled. Your Claude install is untouched.")


@cli.command()
def voices():
    """List available voice characters."""
    from nhj.characters import Character
    t = Table(title="NHJ Voices", show_header=True)
    t.add_column("Name"); t.add_column("Voice"); t.add_column("Tier"); t.add_column("ref.wav"); t.add_column("Triggers")
    for d in sorted(resources.character_defs_dir().iterdir()):
        if not d.is_dir():
            continue
        try:
            c = Character.load(d.name)
            ref_ok = "✓" if c.ref_wav() else "✗ missing"
            t.add_row(c.name, c.voice, c.model_tier, ref_ok, ", ".join(c.triggers_on) or "all")
        except Exception:
            t.add_row(d.name, "?", "?", "?", "?")
    Console().print(t)


@cli.command()
def devices():
    """List configured notification adapters."""
    from nhj.adapters import load_adapters
    from nhj.config import ADAPTER_ORDER
    adapters = load_adapters(ADAPTER_ORDER)
    t = Table(title="NHJ Adapters", show_header=True)
    t.add_column("Adapter"); t.add_column("Status")
    for a in adapters:
        t.add_row(a.__class__.__name__, "[green]ready[/green]")
    if not adapters:
        Console().print("[yellow]No adapters available — check .env configuration[/yellow]")
    else:
        Console().print(t)


@cli.command("build-bank")
def build_bank(
    voice: str = typer.Option("jan", "--voice", "-v"),
    max_takes: int = typer.Option(5, "--max-takes"),
    tts_url: str = typer.Option("http://localhost:9992", "--tts-url"),
):
    """Pre-render audio bank clips for a voice character."""
    from nhj.characters import Character
    from nhj.adapters.audio import synth_qwen, _CLIPS_ROOT

    try:
        char = Character.load(voice)
    except FileNotFoundError:
        app.print(f"[red]No character {voice!r} found in voices/[/red]")
        raise typer.Exit(1)

    total = 0
    for intent, phrases in char.phrases.items():
        out_dir = _CLIPS_ROOT / char.voice / intent
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, phrase in enumerate(phrases):
            out_path = out_dir / f"{i:02d}-{phrase[:30].replace(' ','-').lower()}.wav"
            if out_path.exists():
                app.print(f"  [dim]skip[/dim] {out_path.name}")
                continue
            app.print(f"  synth [{intent}] {phrase[:50]!r}...")
            wav = synth_qwen(phrase, tts_url, voice=char.voice,
                             model_tier=char.model_tier)
            if wav:
                out_path.write_bytes(wav)
                total += 1
            else:
                app.print("    [red]failed[/red]")
    app.print(f"[green]✓[/green] Built {total} clips for [bold]{voice}[/bold]")


@cli.command("start-server")
def start_server(
    port: int = typer.Option(9992, "--port"),
    tier: str = typer.Option("fast", "--tier"),
):
    """Start the Qwen3-TTS server (runs in foreground)."""
    subprocess.run([str(_PYTHON), "-m", "nhj.tts_server",
                    "--port", str(port), "--load", tier])


# ---------------------------------------------------------------------------
# Live settings — change without editing config or restarting (worker reads on
# the next vibe). Backed by nhj.state (platform state dir, with legacy read fallback).
# ---------------------------------------------------------------------------
def _resolve_dial(dial: str, value: int):
    """Thin wrapper around state.resolve_dial (single source of truth)."""
    from nhj.state import resolve_dial
    return resolve_dial(dial, value)


@cli.command("set")
def set_dial_cmd(
    key: str = typer.Argument(..., help="char.dial — jan.ockerism|competence, bazza.stress|competence, karren.karren"),
    value: int = typer.Argument(..., help="intensity 1-11, competence 1-10"),
):
    """Set a character dial live, e.g. `nhj set jan.ockerism 11` or `nhj set jan.competence 3`."""
    from nhj.state import set_dial
    if "." not in key:
        app.print("[red]key must be <character>.<dial> (e.g. jan.ockerism)[/red]"); raise typer.Exit(1)
    char, dial = key.split(".", 1)
    field, stored, shown = _resolve_dial(dial, int(value))
    set_dial(char, field, stored)
    extra = " (swearing — bleeped unless `nhj censor off`)" if field == "level" and stored >= 11 else ""
    app.print(f"[green]✓[/green] {char}.{dial.lower()} = [bold]{shown}[/bold]{extra} (applies on the next vibe)")


def _rave_display(on: bool) -> None:
    """Toggle the AWTRIX party animation for rave mode (best-effort; no-op if no displays)."""
    try:
        from nhj.adapters.ulanzi import UlanziAdapter
        UlanziAdapter().set_rave(on)
    except Exception:
        pass


@cli.command()
def muzak(action: str = typer.Argument("status", help="on | continuous | rave | off | start | stop | status")):
    """Inference muzak — plays while the agent is busy, pauses to speak. ON by default.

    on = on-hold mode (silence when idle, 'hold please' then music while busy, pause to
    speak); continuous = true background music (never auto-pauses); rave = MUZAK RAVE PARTY
    (continuous, full volume — only ducks under TTS alerts — plus a party animation on the
    displays); off = silent. start/stop drive the player directly.
    """
    from nhj.state import set_flag
    from nhj import inference_muzak as im
    a = action.lower()
    if a in ("on", "true", "1", "yes"):
        set_flag("muzak", True); set_flag("muzak_continuous", False); set_flag("muzak_rave", False)
        _rave_display(False); im.enable()                    # clear any leftover stop flag (off→on wedge)
        app.print("[green]✓[/green] inference muzak [bold]ON[/bold] (on-hold: silence when idle, music while busy)")
    elif a in ("continuous", "cont", "background", "bg"):
        set_flag("muzak", True); set_flag("muzak_continuous", True); set_flag("muzak_rave", False)
        _rave_display(False); im.enable()                    # clear stop + start the bed now (continuous)
        app.print("[green]✓[/green] inference muzak [bold]CONTINUOUS[/bold] (background — never auto-pauses)")
    elif a in ("rave", "party"):
        from nhj import modes as M
        M.apply_mode("rave")
        app.print("[magenta]🎉 MUZAK RAVE PARTY[/magenta] (= [bold]nhj mode rave[/bold]) — full-volume grab-bag "
                  "(still ducks under alerts) + display party mode. Run [bold]nhj mode normal[/bold] to stop.")
    elif a in ("off", "false", "0", "no"):
        set_flag("muzak", False); set_flag("muzak_continuous", False); set_flag("muzak_rave", False)
        _rave_display(False); im.stop()
        app.print("[green]✓[/green] inference muzak [bold]OFF[/bold]")
    elif a == "start":
        app.print("[green]♪[/green] inference muzak started (manual)" if im.start()
                  else "[red]no tracks found[/red] (add audio to audio/music/ or set NHJ_MUZAK_DIR)")
    elif a == "stop":
        im.stop();   app.print("[green]⏹[/green] stopped")
    else:
        s = im.status()
        state = "playing" if s["playing"] else "running (idle)" if s["running"] else "stopped"
        app.print(f"[bold]inference muzak:[/bold] {state}  ·  {s['sessions']} busy session(s)  ·  {s['tracks']} tracks")


@cli.command()
def scenes(action: str = typer.Argument("status", help="on | off | list | status")):
    """Easter-egg hold scenes — every so often Jan goes off on a vignette (pub, party, durry)
    with an ambient bed instead of a plain 'hold please'. Rare by design; on by default."""
    from nhj.state import set_flag, get_flag
    from nhj import scenes as sc
    a = action.lower()
    if a in ("on", "true", "1", "yes"):
        set_flag("scenes", True); app.print("[green]✓[/green] hold scenes [bold]ON[/bold] (rare; set NHJ_SCENES_PROB to tune)")
    elif a in ("off", "false", "0", "no"):
        set_flag("scenes", False); app.print("[green]✓[/green] hold scenes [bold]OFF[/bold]")
    elif a == "list":
        for s in sc.SCENES:
            has = "✓" if sc.scene_clip(s["key"]) else "—"
            app.print(f"  [{has}] [bold]{s['key']}[/bold] ({s['ambient'] or 'no bed'}): {s['line']}")
    else:
        on = get_flag("scenes", True)
        rendered = sum(1 for s in sc.SCENES if sc.scene_clip(s["key"]))
        app.print(f"[bold]hold scenes:[/bold] {'on' if on else 'off'}  ·  {rendered}/{len(sc.SCENES)} rendered")


@cli.command("build-scenes")
def build_scenes(
    voice: str = typer.Option("jan", "--voice", "-v"),
    tts_url: str = typer.Option("http://localhost:9992", "--tts-url"),
    beds: bool = typer.Option(True, "--beds/--no-beds", help="fill in MISSING placeholder ambient beds"),
    force_beds: bool = typer.Option(False, "--force-beds", help="overwrite ALL beds with placeholders (destroys real recordings)"),
):
    """Render the scene lines (in `voice`); fill in any missing placeholder ambient beds.

    By default this never overwrites an existing bed — drop real recordings into
    audio/ambient/ and they survive re-renders. Use --force-beds only to
    regenerate the synthetic placeholders from scratch.
    """
    from nhj import scenes as sc
    app.print("Rendering scene lines…")
    n = sc.render_lines(tts_url=tts_url, voice=voice, verbose=True)
    app.print(f"[green]✓[/green] {n} scene line(s) → audio/voice/scenes/")
    if beds or force_beds:
        m = sc.generate_placeholder_beds(overwrite=force_beds, verbose=True)
        m += sc.generate_callcentre_bed(overwrite=force_beds, verbose=True)
        kind = "regenerated" if force_beds else "missing"
        app.print(f"[green]✓[/green] {m} placeholder bed(s) {kind} → audio/ambient/  (real recordings preserved)")


_MODE_BLURB = {
    "normal": "full hold-music, normal voice, default dials.",
    "rave": "full-volume party muzak + display animation.",
    "call-centre": "phone-line voice, tinny hold music, and a looping call-centre room bed.",
    "quiet": "no music, displays off — haptic-only heads-down focus.",
    "special-forces": "whispered comms voice + simple haptics, plus a looping radio bed.",
    "went-full-bogan": "max UNBLEEPED swearing, razor-sharp, no stupid questions — friendly & playful.",
}


@cli.command()
def mode(value: str = typer.Argument("status", help="normal | rave | call-centre | quiet | special-forces | went-full-bogan | status")):
    """Switch the whole experience at once. A MODE is a macro that sets muzak, audio FX,
    persona, dials, the displays, the voice and the haptics together — and clears whatever
    the last mode left behind (so rave and call-centre can't stack). Presets live in
    config/default.yaml `modes:`. Granular commands (`nhj set`, `nhj muzak`) tweak within a mode.
    """
    from nhj import modes as M
    v = M.canonical(value)
    if v in ("status", ""):
        cur = M.current_mode()
        app.print(f"[bold]mode:[/bold] {cur}  ·  available: {', '.join(M.list_modes())}")
        return
    try:
        M.apply_mode(v)
    except KeyError:
        app.print(f"[red]unknown mode {value!r}[/red] — {', '.join(M.list_modes())}")
        raise typer.Exit(1)
    app.print(f"[green]✓[/green] mode [bold]{v}[/bold] — {_MODE_BLURB.get(v, 'applied')} (next vibe)")


@cli.command()
def callcentre(action: str = typer.Argument("on", help="on | off")):
    """Government Call Centre Mode — phone-compressed audio + tinny hold music + a bored,
    apathetic, mildly rude operator who can't really help and is plainly annoyed you rang.
    Alias for `nhj mode call-centre`. `off` restores `nhj mode normal`.
    """
    from nhj import modes as M
    if action.lower() in ("off", "false", "0", "no"):
        M.apply_mode("normal")
        app.print("[green]✓[/green] Government Call Centre Mode [bold]OFF[/bold] — back to normal.")
    else:
        M.apply_mode("call-centre")
        app.print("[cyan]☎️  Government Call Centre Mode [bold]ON[/bold][/cyan] (= [bold]nhj mode call-centre[/bold]) — "
                  "'your call is important to us…' Phone-line audio + looping room tone + tinny hold music, maximum "
                  "incompetence, minimal enthusiasm. (needs NHJ_DYNAMIC for the full apathetic persona)")


@cli.command()
def mute():
    """Silence all feedback until `nhj unmute`."""
    from nhj.state import set_flag
    set_flag("mute", True); app.print("[yellow]🔇 muted[/yellow] — run `nhj unmute` to restore")


@cli.command()
def unmute():
    """Restore feedback after a mute."""
    from nhj.state import set_flag
    set_flag("mute", False); app.print("[green]🔊 unmuted[/green]")


@cli.command()
def swearing(state: str = typer.Argument("on", help="on | off")):
    """Full swearing bogan = Jan's ockerism 11. Swears are bleeped unless `nhj censor off`."""
    from nhj.state import set_dial
    on = state.lower() in ("on", "true", "1", "yes")
    set_dial("jan", "level", 11 if on else 10)
    app.print(f"[green]✓[/green] Jan ockerism = [bold]{'11 (swearing)' if on else '10'}[/bold]")


@cli.command()
def censor(mode: str = typer.Argument("quack", help="quack | beep | honk | off")):
    """Bleep strong swears with a spoken token (default quack). `off` = raw swears."""
    from nhj.state import set_setting
    m = mode.lower()
    if m not in ("quack", "beep", "honk", "off"):
        app.print("[red]mode must be quack | beep | honk | off[/red]"); raise typer.Exit(1)
    set_setting("censor", m)
    note = " — swears spoken raw" if m == "off" else f" — strong swears become “{m}”"
    app.print(f"[green]✓[/green] censor = [bold]{m}[/bold]{note}")


@cli.command()
def status():
    """Show current effective settings."""
    from nhj.state import load
    from nhj.characters import Character, _dial_for, _cfg
    st = load(); flags = st.get("flags", {})
    cfg_chars = _cfg().get("characters", {})
    settings = st.get("settings", {})
    t = Table(title="Not-Happy-Jan")
    t.add_column("setting", style="cyan"); t.add_column("value")
    from nhj.modes import current_mode, list_modes
    t.add_row("mode", f"[bold magenta]{current_mode()}[/bold magenta]  "
                      f"[dim]({', '.join(list_modes())})[/dim]")
    t.add_row("mute", "[yellow]ON[/yellow]" if flags.get("mute") else "off")
    audio_mode = settings.get("audio_mode", "normal")
    extras = []
    if audio_mode != "normal":
        extras.append(f"audio [bold]{audio_mode}[/bold]")
    if (settings.get("display", "normal") or "normal") != "normal":
        extras.append(f"display [bold]{settings['display']}[/bold]")
    if settings.get("voice_override"):
        extras.append(f"voice [bold]{settings['voice_override']}[/bold]")
    if settings.get("ambient_mode"):
        extras.append(f"ambient [bold]{settings['ambient_mode']}[/bold]")
    if (settings.get("haptic_style", "full") or "full") != "full":
        extras.append(f"haptic [bold]{settings['haptic_style']}[/bold]")
    if extras:
        t.add_row("scene overrides", "  ·  ".join(extras))
    if flags.get("muzak_rave"):
        _muzak_state = "[magenta]RAVE PARTY 🎉[/magenta]"
    elif flags.get("muzak_continuous"):
        _muzak_state = "[green]CONTINUOUS[/green]"
    elif flags.get("muzak", True):          # on by default
        _muzak_state = "[yellow]ON[/yellow]"
    else:
        _muzak_state = "off"
    t.add_row("inference muzak", _muzak_state)
    from nhj import censor as _censor
    t.add_row("censor", _censor.mode())
    from dotenv import load_dotenv
    load_dotenv(resources.env_file())
    if os.getenv("NHJ_DYNAMIC", "").strip().lower() in ("1", "true", "yes", "on"):
        t.add_row("dynamic", f"[green]ON[/green] · {os.getenv('NHJ_DYNAMIC_MODEL', 'local')}"
                             f" @ {os.getenv('NHJ_DYNAMIC_BASE_URL', 'localhost')}")
    else:
        t.add_row("dynamic", "off (static phrase banks)")
    friendly = {"jan": ("ockerism", "competence"),
                "bazza": ("stress", "competence"),
                "karren": ("karren", None)}
    for name in ("jan", "bazza", "karren"):
        try:
            ch = Character.load(name)
            lvl = _dial_for(name, cfg_chars, "level", ch.level)
            iname, cname = friendly[name]
            row = f"{iname} {lvl}" + (" [yellow](swearing)[/yellow]" if lvl >= 11 else "")
            if cname:
                comp = 10 - _dial_for(name, cfg_chars, "chaos", ch.chaos)
                row += f"  ·  {cname} {comp}"
            t.add_row(name, row)
        except Exception:
            pass
    app.print(t)


@cli.command("dynamic-test")
def dynamic_test(
    message: str = typer.Option("the build passed", "--message", "-m"),
    intent: str = typer.Option("ok", "--intent", "-i"),
    character: str = typer.Option("jan", "--character", "-c"),
    base_url: str = typer.Option("", "--base-url", help="override NHJ_DYNAMIC_BASE_URL"),
    model: str = typer.Option("", "--model", help="override NHJ_DYNAMIC_MODEL"),
):
    """Sanity-check a dynamic LLM endpoint: generate one in-character line and show it."""
    import time
    from dotenv import load_dotenv
    load_dotenv(resources.env_file())
    if base_url:
        os.environ["NHJ_DYNAMIC_BASE_URL"] = base_url
    if model:
        os.environ["NHJ_DYNAMIC_MODEL"] = model
    os.environ["NHJ_DYNAMIC"] = "1"  # force-enable for this test

    from nhj import boganify, censor
    from nhj.characters import Character, _dial_for, _cfg, SWEAR_LEVEL
    try:
        ch = Character.load(character)
    except Exception:
        app.print(f"[red]no such character: {character}[/red]"); raise typer.Exit(1)
    cfg_chars = _cfg().get("characters", {})
    ch.level = _dial_for(ch.name, cfg_chars, "level", ch.level)
    ch.chaos = _dial_for(ch.name, cfg_chars, "chaos", ch.chaos)
    swearing = ch.level >= SWEAR_LEVEL

    ep = f"{os.getenv('NHJ_DYNAMIC_MODEL', 'local')} @ {os.getenv('NHJ_DYNAMIC_BASE_URL', 'http://localhost:9991/v1')}"
    app.print(f"[dim]endpoint:[/dim] {ep}")
    app.print(f"[dim]character:[/dim] {ch.name} (level {ch.level}, competence {10 - ch.chaos}, swearing {'on' if swearing else 'off'})  ·  intent {intent}  ·  \"{message}\"")
    t0 = time.monotonic()
    text = boganify.generate(ch, intent, message, ch.level, swearing, ch._band().instruct, ch.chaos)
    dt = time.monotonic() - t0
    if text:
        spoken = censor.apply(text)
        app.print(f"[green]✓ generated[/green] ({dt:.1f}s):  [bold]{text}[/bold]")
        if spoken != text:
            app.print(f"  [dim]spoken (censor={censor.mode()}):[/dim] [bold]{spoken}[/bold]")
    else:
        app.print(f"[red]✗ no response[/red] ({dt:.1f}s) — NHJ would fall back to the static bank. Check the endpoint/model are up.")


def _set_env_vars(env_path: Path, kv: dict) -> None:
    """Idempotently set KEY=value lines in .env (replace in place or append)."""
    env_path.parent.mkdir(parents=True, exist_ok=True)   # the user config dir may not exist yet
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    for key, val in kv.items():
        repl = f"{key}={val}"
        for i, ln in enumerate(lines):
            if ln.strip().startswith(f"{key}=") or ln.strip().startswith(f"# {key}="):
                lines[i] = repl
                break
        else:
            lines.append(repl)
    env_path.write_text("\n".join(lines) + "\n")


_OCKER_LABEL = "com.guruswami.nhj-ocker-bogan-nano"


def _install_ocker_launchagent(gguf_path: str, port: int) -> Path:
    """Write + (re)load a LaunchAgent serving ocker-bogan-nano via llama-server on the given port."""
    import shutil
    import sys
    llama = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
    # Launch llama-server under nhj.llm_server so the supervised process shows as
    # "NHJ LLM" (not the generic "llama-server") in Activity Monitor / login items.
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{_OCKER_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string>
    <string>-m</string><string>nhj.llm_server</string>
    <string>{llama}</string>
    <string>-m</string><string>{gguf_path}</string>
    <string>--alias</string><string>ocker-bogan-nano</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>{port}</string>
    <string>-c</string><string>2048</string>
    <string>--no-webui</string>
  </array>
  <key>WorkingDirectory</key><string>{resources.data_dir()}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{resources.log_dir() / "ocker-bogan-nano.log"}</string>
  <key>StandardErrorPath</key><string>{resources.log_dir() / "ocker-bogan-nano.log"}</string>
</dict></plist>
"""
    dest = Path.home() / "Library" / "LaunchAgents" / f"{_OCKER_LABEL}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(plist)
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_OCKER_LABEL}"],
                   capture_output=True)  # ignore "not loaded"
    import time; time.sleep(1)                        # let the old job fully tear down (avoid bootstrap race)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True)
    return dest


_TTS_LABEL = "com.guruswami.nhj-tts"


def _install_tts_launchagent(port: int) -> Path:
    """Write + (re)load a LaunchAgent running the Qwen3-TTS server (nhj.tts_server), bound to
    127.0.0.1 on the given port. Paths are COMPUTED (this venv's python + the repo root) — no
    static install path is baked in, so it works wherever the repo lives."""
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{_TTS_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{_PYTHON}</string>
    <string>-m</string><string>nhj.tts_server</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>{port}</string>
  </array>
  <key>WorkingDirectory</key><string>{resources.data_dir()}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{resources.log_dir() / "tts.log"}</string>
  <key>StandardErrorPath</key><string>{resources.log_dir() / "tts.log"}</string>
</dict></plist>
"""
    dest = Path.home() / "Library" / "LaunchAgents" / f"{_TTS_LABEL}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(plist)
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_TTS_LABEL}"], capture_output=True)
    import time; time.sleep(1)                        # let the old job fully tear down (avoid bootstrap race)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True)
    return dest


@cli.command("install-tts")
def install_tts(
    port: int = typer.Option(int(os.getenv("NHJ_TTS_PORT", "9992")), "--port"),
):
    """Install the Qwen3-TTS voice server as a LaunchAgent (auto-start + KeepAlive, macOS).

    Generates the plist from computed paths (this venv's python + the repo root) and binds
    127.0.0.1 — no static path to edit. Apple Silicon only (the voice model is MLX).
    """
    import platform
    if platform.system() != "Darwin":
        app.print("[yellow]The persistent TTS LaunchAgent is macOS-only.[/yellow] "
                  "Run it yourself instead: [bold]nhj start-server[/bold].")
        raise typer.Exit(1)
    dest = _install_tts_launchagent(port)
    _set_env_vars(resources.env_file(), {"NHJ_TTS_URL": f"http://127.0.0.1:{port}"})
    app.print(f"[green]✓[/green] TTS LaunchAgent loaded ({dest.name}) — 127.0.0.1:{port}, "
              f"starts on login + now")
    app.print("test it:  [bold]nhj test ok[/bold]")


@cli.command("install-model")
def install_model(
    repo: str = typer.Option("guruswami-ai/ocker-bogan-nano", "--repo",
                             help="Hugging Face repo for the ocker-bogan-nano GGUF model"),
    gguf: str = typer.Option("ocker-bogan-nano-Q4_K_M.gguf", "--gguf",
                             help="GGUF filename within the repo"),
    port: int = typer.Option(9991, "--port"),
    service: bool = typer.Option(True, "--service/--no-service",
                                 help="install a LaunchAgent so the server is always up (macOS)"),
):
    """Install ocker-bogan-nano — NHJ's bundled fine-tuned voice brain for dynamic mode.

    A ~1.5B Qwen2.5-abliterated fine-tune (~940 MB, Q4_K_M GGUF) that rephrases each
    notification in character. Downloads it from Hugging Face, points .env at a local
    llama.cpp server, and (macOS) installs a LaunchAgent so the endpoint is always running.
    Cross-platform — needs `llama-server` (brew install llama.cpp, or build llama.cpp).
    """
    import shutil
    if not shutil.which("llama-server"):
        app.print("[yellow]llama-server (llama.cpp) not found.[/yellow] Install it with "
                  "[bold]brew install llama.cpp[/bold] (macOS) or build from "
                  "https://github.com/ggml-org/llama.cpp — then re-run.")
        raise typer.Exit(1)

    cache = resources.llm_model_dir()
    app.print(f"[cyan]↓[/cyan] downloading [bold]{repo}/{gguf}[/bold] → {cache} …")
    try:
        from huggingface_hub import hf_hub_download
        model_path = hf_hub_download(repo_id=repo, filename=gguf, local_dir=str(cache))
    except Exception as e:
        app.print(f"[red]download failed:[/red] {str(e)[:120]}")
        app.print("[dim]Private/not-published yet? Point NHJ_DYNAMIC_* at your own "
                  "OpenAI-compatible endpoint — see docs/dynamic-voices.md.[/dim]")
        raise typer.Exit(1)

    _set_env_vars(resources.env_file(), {
        "NHJ_DYNAMIC": "1",
        "NHJ_DYNAMIC_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "NHJ_DYNAMIC_MODEL": "ocker-bogan-nano",
    })
    app.print(f"[green]✓[/green] model ready  ·  [green]✓[/green] .env → dynamic mode on (:{port})")

    import platform
    if service and platform.system() == "Darwin":
        dest = _install_ocker_launchagent(model_path, port)
        app.print(f"[green]✓[/green] LaunchAgent loaded ({dest.name}) — starts on login + now")
    else:
        if service:        # --service requested on a non-macOS host (LaunchAgents are macOS-only)
            app.print("[yellow]--service is macOS-only (LaunchAgent); run the server yourself "
                      "or use your platform's service manager:[/yellow]")
        app.print(f"[dim]serve it:  llama-server -m {model_path} --alias ocker-bogan-nano "
                  f"--host 127.0.0.1 --port {port} -c 2048[/dim]")
    app.print("test it:  [bold]nhj dynamic-test[/bold]")


def _safe_extract_tar(tf: tarfile.TarFile, destination: Path) -> None:
    """Extract regular files/directories without allowing archive path escapes."""
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    members = tf.getmembers()
    for member in members:
        target = (root / member.name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"unsafe archive path: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member: {member.name}")
    # The `data` filter strips archive-controlled metadata (modes/ownership/special bits) —
    # the safe default on 3.12+, backported to recent 3.10/3.11. Fall back where unavailable.
    try:
        tf.extractall(root, members=members, filter="data")
    except TypeError:
        tf.extractall(root, members=members)


@cli.command("setup-media")
def setup_media(
    base: str = typer.Option("", "--base", help="media base URL (default: $NHJ_MEDIA_BASE or the GitHub release assets)"),
    force: bool = typer.Option(False, "--force", help="re-download even if already present"),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="exit non-zero if any media bundle cannot be installed",
    ),
):
    """Download the bundled media — character voices + audio (music/sfx/beds).

    Voices (~3 MB) and the audio bundle (~250 MB) aren't in git; they're attached as
    assets on the GitHub release. This pulls + extracts them into voices/ and audio/. Idempotent.

    The plain asset URL works once the repo (or NHJ_MEDIA_BASE) is public. While the repo
    is PRIVATE the URL 404s (release assets need auth), so this falls back to
    `gh release download` when gh is installed and authenticated.
    """
    import re
    import shutil
    import tempfile
    import urllib.error
    import urllib.request
    base = (base or os.getenv("NHJ_MEDIA_BASE")
            or "https://github.com/guruswami-ai/not-happy-jan/releases/download/media").rstrip("/")
    # Parse owner/repo + tag from a GitHub release base, for the authenticated fallback.
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)", base)
    gh_repo, gh_tag = (f"{m.group(1)}/{m.group(2)}", m.group(3)) if m else (None, None)
    gh = shutil.which("gh")

    data_root = resources.data_dir()                   # repo root in a checkout, else the platform data dir
    bundles = [
        ("voices.tar.gz",  resources.voices_dir(),  resources.voices_dir() / "jan" / "ref.wav"),
        ("audio.tar.gz",   resources.audio_dir(),   resources.audio_dir() / "music"),
    ]
    failures = 0
    for name, dest, sentinel in bundles:
        if sentinel.exists() and not force:
            app.print(f"[dim]✓ {name} already present ({sentinel.name}) — skip (use --force to refresh)[/dim]")
            continue
        with tempfile.TemporaryDirectory() as td:
            tarpath = Path(td) / name
            got = False
            url = f"{base}/{name}"
            app.print(f"[cyan]↓[/cyan] {url} …")
            try:                                       # 1) plain URL — public repo / public NHJ_MEDIA_BASE
                urllib.request.urlretrieve(url, tarpath)
                got = True
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404) and gh and gh_repo:   # 2) private repo → authenticated gh
                    app.print(f"[dim]  HTTP {e.code} (private repo?) — retrying via gh release download…[/dim]")
                    r = subprocess.run([gh, "release", "download", gh_tag, "-R", gh_repo,
                                        "-p", name, "-D", td, "--clobber"],
                                       capture_output=True, text=True)
                    got = (r.returncode == 0 and tarpath.exists())
                    if not got:
                        app.print(f"[red]  gh fallback failed:[/red] {(r.stderr or '').strip()[:160]}")
                else:
                    app.print(f"[red]failed:[/red] HTTP {e.code}")
            except Exception as e:
                app.print(f"[red]failed:[/red] {str(e)[:120]}")

            if not got:
                hint = ("install + authenticate `gh` (gh auth login)" if (gh_repo and not gh)
                        else "set NHJ_MEDIA_BASE to a public host, or make the repo public")
                app.print(f"[dim]  Could not fetch {name} — {hint}. NHJ still runs without media "
                          f"(nhj muzak off; add voices/<name>/ref.{{wav,txt}} manually).[/dim]")
                failures += 1
                continue
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tarpath) as tf:
                _safe_extract_tar(tf, data_root)       # bundles are rooted at voices/ or audio/
            app.print(f"[green]✓[/green] {name} → {dest.relative_to(data_root)}/")
    if strict and failures:
        raise typer.Exit(code=1)
    app.print("done — [bold]nhj voices[/bold] to confirm, [bold]nhj test ok[/bold] to hear it.")


@cli.command("servers")
def servers_cmd(choice: str = typer.Argument("status", help="persistent | on-demand | status")):
    """Choose how the TTS/LLM model servers run.

    persistent — always warm (KeepAlive LaunchAgents; ~3.2 GB resident, instant replies).
    on-demand  — start on the first vibe, unload after idle (RAM only while NHJ is talking).
    status     — show the current mode and which servers are up.
    """
    import platform
    from nhj import servers as S
    from nhj.state import get_setting, set_setting

    choice = choice.strip().lower().replace("ondemand", "on-demand")
    if choice in ("persistent", "on-demand"):
        set_setting("servers", choice)
        labels = (_TTS_LABEL, _OCKER_LABEL)
        if platform.system() == "Darwin":
            uid = str(os.getuid())
            if choice == "on-demand":
                for lbl in labels:                          # free the model RAM now
                    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{lbl}"], capture_output=True)
                app.print("[green]✓[/green] on-demand — daemons stopped; the TTS/LLM start on the "
                          "next vibe and unload after idle (NHJ_SERVER_IDLE, default 600s).")
            else:
                for lbl in labels:                          # bring the always-warm daemons back
                    plist = Path.home() / "Library" / "LaunchAgents" / f"{lbl}.plist"
                    if plist.exists():
                        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                                       capture_output=True)
                app.print("[green]✓[/green] persistent — daemons (re)loaded; always warm.")
        else:
            app.print(f"[green]✓[/green] servers = {choice}")
        return

    app.print(f"servers mode: [bold]{get_setting('servers', 'persistent')}[/bold]")
    for name in ("tts", "llm"):
        app.print(f"  {name:<4} {'[green]up[/green]' if S.is_up(name) else '[dim]down[/dim]'}")


# ---------------------------------------------------------------------------
# MCP server registration with Claude Code
# ---------------------------------------------------------------------------
_MCP_SERVER_NAME = "not-happy-jan"
_MCP_LABEL = "com.guruswami.nhj-mcp"
_VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")


def _install_mcp_launchagent(listen: str, port: int) -> Path:
    """Write and load the persistent streamable-HTTP MCP LaunchAgent."""
    import plistlib

    resources.log_dir()
    payload = {
        "Label": _MCP_LABEL,
        "ProgramArguments": [str(_PYTHON), "-m", "nhj.mcp_server"],
        "WorkingDirectory": str(resources.data_dir()),
        "EnvironmentVariables": {
            "NHJ_TRANSPORT": "streamable-http",
            "NHJ_HOST": listen,
            "NHJ_PORT": str(port),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(resources.log_dir() / "mcp.log"),
        "StandardErrorPath": str(resources.log_dir() / "mcp.log"),
    }
    dest = Path.home() / "Library" / "LaunchAgents" / f"{_MCP_LABEL}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))

    uid = str(os.getuid())
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{_MCP_LABEL}"],
        capture_output=True,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(dest)],
        capture_output=True,
        text=True,
    )
    if loaded.returncode != 0:
        raise RuntimeError((loaded.stderr or loaded.stdout or "launchctl bootstrap failed").strip())
    return dest


@cli.command("install-mcp-service")
def install_mcp_service(
    listen: str = typer.Option(
        "127.0.0.1",
        "--listen",
        help="Bind address (default 127.0.0.1; network exposure is explicit).",
    ),
    port: int = typer.Option(8765, "--port"),
):
    """Install a persistent streamable-HTTP MCP LaunchAgent on macOS."""
    import platform

    if platform.system() != "Darwin":
        app.print("[yellow]The persistent MCP LaunchAgent is macOS-only.[/yellow]")
        raise typer.Exit(1)
    try:
        dest = _install_mcp_launchagent(listen, port)
    except (OSError, RuntimeError) as exc:
        app.print(f"[red]MCP LaunchAgent installation failed:[/red] {exc}")
        raise typer.Exit(1)
    app.print(
        f"[green]✓[/green] MCP LaunchAgent loaded ({dest.name}) — "
        f"streamable-http on {listen}:{port}"
    )
    if listen not in ("127.0.0.1", "::1", "localhost"):
        app.print(
            f"[yellow]Warning:[/yellow] MCP is network-accessible on {listen}; "
            "configure authentication/firewall controls before use."
        )


@cli.command("install-mcp")
def install_mcp(
    settings: Path = typer.Option(
        Path.home() / ".claude" / "settings.json",
        help="Path to Claude Code settings.json",
    ),
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="MCP transport: stdio (default, local) | sse | streamable-http",
    ),
    listen: str = typer.Option(
        "127.0.0.1",
        "--listen",
        help="Bind address for network transports (default 127.0.0.1). "
             "Use 0.0.0.0 only with explicit intent to expose to the network.",
    ),
    port: int = typer.Option(8765, "--port", help="Port for network transports."),
):
    """Register the NHJ MCP server with Claude Code.

    stdio is correct for local Claude Code sessions (on-demand, per-session).
    sse/streamable-http is for a persistent network daemon — still bound to
    127.0.0.1 by default. Network exposure (0.0.0.0 or LAN address) requires
    an explicit --listen flag.
    """
    t = transport.lower().strip()
    if t not in _VALID_TRANSPORTS:
        app.print(f"[red]unknown transport {transport!r}[/red] — "
                  f"use one of: {', '.join(_VALID_TRANSPORTS)}")
        raise typer.Exit(1)

    cfg: dict = {}
    if settings.exists():
        try:
            cfg = json.loads(settings.read_text())
        except json.JSONDecodeError:
            pass
        bak = settings.with_suffix(".json.pre-nhj-mcp.bak")
        if not bak.exists():
            bak.write_text(settings.read_text())
            app.print(f"[dim]backup → {bak.name}[/dim]")

    mcp_servers = cfg.setdefault("mcpServers", {})

    if t == "stdio":
        py = str(_PYTHON)
        mcp_servers[_MCP_SERVER_NAME] = {
            "command": py,
            "args": ["-m", "nhj.mcp_server"],
        }
        app.print("[green]✓[/green] MCP server registered (stdio, local):")
        app.print(f"  [dim]command:[/dim] {py} -m nhj.mcp_server")
    else:
        # Network transport — 127.0.0.1 by default; LAN requires deliberate --listen
        url = f"http://{listen}:{port}"
        if t == "sse":
            url_path = f"{url}/sse"
        else:
            url_path = f"{url}/mcp"
        mcp_servers[_MCP_SERVER_NAME] = {
            "transport": t,
            "url": url_path,
        }
        app.print(f"[green]✓[/green] MCP server registered ({t}, {listen}:{port}):")
        app.print(f"  [dim]url:[/dim] {url_path}")
        if listen not in ("127.0.0.1", "::1", "localhost"):
            app.print(f"  [yellow]⚠[/yellow]  MCP server is accessible on the network ({listen}). "
                      f"Ensure your firewall is configured appropriately.")

    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(cfg, indent=2))
    app.print(f"  [dim]→ {settings}  ·  restart Claude Code to activate[/dim]")


@cli.command("remove-mcp")
def remove_mcp(
    settings: Path = typer.Option(
        Path.home() / ".claude" / "settings.json",
        help="Path to Claude Code settings.json",
    ),
):
    """Remove the NHJ MCP server registration from Claude Code settings."""
    if not settings.exists():
        app.print("[yellow]settings.json not found[/yellow]")
        return
    cfg = json.loads(settings.read_text())
    mcp_servers = cfg.get("mcpServers", {})
    if _MCP_SERVER_NAME in mcp_servers:
        del mcp_servers[_MCP_SERVER_NAME]
        settings.write_text(json.dumps(cfg, indent=2))
        app.print(f"[green]✓[/green] NHJ MCP server removed from {settings.name}")
    else:
        app.print(f"[dim]NHJ MCP server not found in {settings.name}[/dim]")


def main():
    cli()


if __name__ == "__main__":
    main()
