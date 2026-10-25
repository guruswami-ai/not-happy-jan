"""`nhj say` (#16 phase 2): speak exactly the given text, end to end through the literal flag."""
from __future__ import annotations

import io

import pytest
from typer.testing import CliRunner

from nhj import cli, event, orchestration, say, worker
from nhj.adapters import audio
from nhj.characters import resolve_character
from nhj.queue_manager import QueueManager


# ---- the event / queue contract -----------------------------------------------------------------
def test_literal_defaults_false_and_is_left_out_of_kwargs_unless_set():
    plain = event.validate("ok", message="hi")
    assert plain.literal is False and "literal" not in plain.to_kwargs()      # old daemons never see it
    lit = event.validate("ok", message="hi", literal=True)
    assert lit.to_kwargs()["literal"] is True


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False), (False, False)])
def test_literal_coercion(raw, expected):
    assert event.validate("ok", literal=raw).literal is expected


def test_literal_rejects_nonsense():
    with pytest.raises(event.VibeValidationError, match="literal"):
        event.validate("ok", literal="maybe")


def test_literal_survives_the_queue_and_old_files_still_parse():
    qm = QueueManager()
    qm.add("ok", message="exact words", literal=True)
    qm.add("ok", message="plain")
    first = qm.claim_next()[1]
    assert first["message"] == "exact words" and first.get("literal") == "1"
    second = qm.claim_next()[1]
    assert second["message"] == "plain" and "literal" not in second           # untouched format for normal events


def test_mcp_tool_accepts_literal(monkeypatch):
    from nhj import mcp_server
    queued = []

    class FakeQM:
        def add(self, **kw):
            queued.append(kw)

        def start_worker_if_needed(self):
            pass
    monkeypatch.setattr(mcp_server, "QueueManager", FakeQM)
    mcp_server.nhj_vibe(intent="ok", message="x", literal=True)
    mcp_server.nhj_vibe(intent="ok", message="y")
    assert queued[0]["literal"] is True and "literal" not in queued[1]


# ---- the words are not changed -----------------------------------------------------------------
def test_literal_delivery_skips_the_llm_templates_and_garbling(monkeypatch):
    ch = resolve_character("ok")
    monkeypatch.setattr(type(ch), "_dynamic", lambda *a, **k: pytest.fail("dynamic LLM must not run"))
    monkeypatch.setattr(type(ch), "_phrase", lambda *a, **k: pytest.fail("phrase templates must not run"))
    ch.chaos = 10                                                        # would garble heavily if it ran
    assert ch.delivery("ok", "  Deploy 42 is done.  ", literal=True).text == "Deploy 42 is done."


def test_literal_delivery_still_applies_the_users_censor(monkeypatch):
    ch = resolve_character("ok")
    monkeypatch.setattr(type(ch), "_censor", lambda self, t: t.replace("heck", "****"))
    assert ch.delivery("ok", "what the heck", literal=True).text == "what the ****"


def test_literal_without_a_message_falls_back_to_the_normal_phrase(monkeypatch):
    ch = resolve_character("ok")
    monkeypatch.setattr(type(ch), "_phrase", lambda self, i, m: "static phrase")
    assert ch.delivery("ok", "", literal=True).text == "static phrase"


def test_worker_passes_literal_and_never_downgrades_or_blanks_it(monkeypatch):
    seen = []

    class FakeAdapter:
        channel = "other"

        def fire(self, **kw):
            seen.append(kw)
    monkeypatch.setattr(orchestration, "voice_mode", lambda intent: "chime")           # another session is busy
    base = {"intent": "ok", "message": "exact words", "verbosity": "low", "source": "proj"}
    worker._dispatch({**base, "literal": "1"}, [FakeAdapter()])
    worker._dispatch(dict(base), [FakeAdapter()])
    lit, normal = seen
    assert lit["literal"] is True and lit["message"] == "exact words" and lit["voice_mode"] == "speak"
    assert normal["literal"] is False and normal["message"] == "" and normal["voice_mode"] == "chime"


def _speak(monkeypatch, message, literal):
    seen = {}
    monkeypatch.setenv("TTS_ENGINE", "audiocpp")
    monkeypatch.setattr(audio, "_audiocpp_cfg", lambda: {})
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: None)
    monkeypatch.setattr(audio, "pick_return_clip", lambda: None)
    monkeypatch.setattr(audio, "pick_bank_clip", lambda v, i: "/bank.wav")
    monkeypatch.setattr(audio, "_normalize_voice_wav", lambda w: w)
    monkeypatch.setattr(audio, "_save_and_cache", lambda *a, **k: None)
    monkeypatch.setattr(audio, "_write_temp_wav", lambda w: "/tmp/x.wav")
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: seen.setdefault("path", path))

    def fake(text, *a, **k):
        seen["text"] = text
        return b"RIFF" + b"\0" * 2000
    monkeypatch.setattr(audio, "synth_audiocpp", fake)
    audio.AudioAdapter().fire("ok", message, resolve_character("ok"), literal=literal)
    return seen


def test_audio_speaks_the_exact_text_and_adds_no_digest_when_literal(monkeypatch):
    orchestration.note_chime("a")
    orchestration.note_chime("b")
    seen = _speak(monkeypatch, "Deploy 42 is done.", literal=True)
    assert seen["text"] == "Deploy 42 is done."                       # no digest sentence, no template
    assert orchestration.peek_digest() == 2                          # and the digest is kept for a normal event


def test_a_normal_event_still_gets_the_digest(monkeypatch):
    orchestration.note_chime("a")
    seen = _speak(monkeypatch, "all done", literal=False)
    assert "1 other task finished while you waited." in seen["text"]


def test_display_adapter_shows_the_exact_text_for_literal(monkeypatch):
    from nhj import boganify
    from nhj.adapters import ulanzi
    monkeypatch.setattr(boganify, "enabled", lambda: True)
    monkeypatch.setattr(boganify, "generate_display", lambda *a, **k: "LLM HEADLINE")
    ad = ulanzi.UlanziAdapter.__new__(ulanzi.UlanziAdapter)
    ad.dynamic_text, ad.text_style = True, "ocker"
    ch = resolve_character("ok")
    assert ad._text("ok", "exact words", ch) == "LLM HEADLINE"
    assert ad._text("ok", "exact words", ch, literal=True) == "exact words"


# ---- the say module ----------------------------------------------------------------------------
@pytest.mark.parametrize("sev,intent", [("success", "ok"), ("INFO", "step"), ("warning", "warn"), ("error", "err"),
                                        ("attention", "attn"), ("celebrate", "celebrate"), ("err", "err")])
def test_severity_aliases(sev, intent):
    assert say.intent_for(sev) == intent


def test_unknown_severity_is_a_usage_error():
    with pytest.raises(say.SayError, match="unknown severity"):
        say.intent_for("catastrophic")


def test_text_is_cleaned_joined_and_bounded():
    assert say.read_text(["build", "done\n\tnow\x07"], io.StringIO(), 240) == "build done now"
    with pytest.raises(say.SayError, match="nothing to say"):
        say.read_text(["  \n "], io.StringIO(), 240)
    with pytest.raises(say.SayError, match="limit is 240"):
        say.read_text(["x" * 241], io.StringIO(), 240)
    assert len(say.read_text(["x" * say.HARD_MAX_CHARS], io.StringIO(), say.HARD_MAX_CHARS)) == say.HARD_MAX_CHARS
    with pytest.raises(say.SayError, match="--max-chars"):
        say.read_text(["hi"], io.StringIO(), say.HARD_MAX_CHARS + 1)


def test_stdin_is_read_only_for_a_lone_dash_and_is_bounded():
    assert say.read_text(["-"], io.StringIO("from a pipe\n"), 240) == "from a pipe"

    class Endless:
        def __init__(self):
            self.asked = 0

        def read(self, n):
            self.asked = n
            return "y" * n
    endless = Endless()
    with pytest.raises(say.SayError, match="before cleaning"):
        say.read_text(["-"], endless, 240)
    assert endless.asked == say.RAW_STDIN_LIMIT + 1                 # never asked for more than the bound + 1
    assert say.read_text(["not", "-"], io.StringIO("ignored"), 240) == "not -"   # "-" only means stdin on its own


def test_padding_before_more_text_is_kept_not_silently_dropped():
    """Codex review on #100: a short message, a long run of blanks, then more words used to be cut off after
    the first window and accepted as just the first message."""
    raw = "hi" + " " * 600 + "and the important part"
    assert say.read_text(["-"], io.StringIO(raw), 240) == "hi and the important part"
    too_wide = "hi" + " " * (say.RAW_STDIN_LIMIT + 50) + "and the important part"
    with pytest.raises(say.SayError, match="before cleaning"):          # refused, never truncated
        say.read_text(["-"], io.StringIO(too_wide), 240)


def test_wait_processed_outcomes(tmp_path):
    stem = "0001-1-000001-aaaa"
    (tmp_path / f"{stem}.vibe").write_text("x")
    assert say.wait_processed(tmp_path, f"{stem}.vibe", 0.05, poll=0.01) == "timeout"
    (tmp_path / f"{stem}.vibe").rename(tmp_path / f"{stem}.proc.123")
    assert say.wait_processed(tmp_path, f"{stem}.vibe", 0.05, poll=0.01) == "timeout"     # still being worked on
    (tmp_path / f"{stem}.proc.123").unlink()
    assert say.wait_processed(tmp_path, f"{stem}.vibe", 0.05, poll=0.01) == "done"
    (tmp_path / f"{stem}.poison").write_text("bad")
    assert say.wait_processed(tmp_path, f"{stem}.vibe", 0.05, poll=0.01) == "rejected"


# ---- the command -------------------------------------------------------------------------------
@pytest.fixture
def queue(monkeypatch, tmp_path):
    """A fake queue that records events; never starts a worker."""
    seen = []

    class FakeQM:
        queue_dir = tmp_path

        def add(self, **kw):
            seen.append(kw)
            return "0001-1-000001-aaaa.vibe"

        def start_worker_if_needed(self):
            pass
    monkeypatch.setattr("nhj.queue_manager.QueueManager", FakeQM)
    monkeypatch.setenv("TTS_ENGINE", "audiocpp")
    return seen


def _say(*args, input=None):
    return CliRunner().invoke(cli.cli, ["say", *args], input=input)


def test_say_queues_a_literal_event_with_the_mapped_intent(queue):
    r = _say("--severity", "error", "--character", "karren", "--", "Deploy", "failed", "on", "node 3")
    assert r.exit_code == 0 and r.stdout == ""                       # prints nothing when it can speak
    assert len(queue) == 1
    ev = queue[0]
    assert (ev["intent"], ev["message"], ev["character"], ev["literal"]) == ("err", "Deploy failed on node 3", "karren", True)


def test_rewrite_hands_the_text_back_to_the_character(queue):
    assert _say("--rewrite", "all", "good").exit_code == 0
    assert "literal" not in queue[0] and queue[0]["message"] == "all good"


def test_options_after_the_text_belong_to_the_text(queue):
    assert _say("see", "--severity", "x").exit_code == 0
    assert queue[0]["message"] == "see --severity x"


def test_usage_errors_exit_2_and_queue_nothing(queue):
    for args in (["--severity", "bogus", "hi"], ["x" * 241], ["--max-chars", "5", "toolong"], ["  "]):
        r = _say(*args)
        assert r.exit_code == 2, args
    assert queue == []


def test_stdin_with_a_dash(queue):
    assert _say("-", input="piped text\n").exit_code == 0
    assert queue[0]["message"] == "piped text"


def test_muted_prints_the_text_to_stderr_but_still_succeeds(queue):
    from nhj.state import set_flag
    set_flag("mute", True)
    r = _say("it", "is", "done")
    assert r.exit_code == 0 and "it is done" in r.output
    assert len(queue) == 1                                           # displays and the board still get it


def test_strict_fails_with_10_when_it_cannot_be_spoken(queue, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    r = _say("--strict", "hello")
    assert r.exit_code == 10 and "hello" in r.output and "not spoken" in r.output
    assert _say("hello").exit_code == 0                              # without --strict: delivered by text


def test_voice_off_for_the_project_counts_as_unspeakable(queue, monkeypatch):
    from nhj import gate
    monkeypatch.setattr(gate, "voice_allowed", lambda source: False)
    r = _say("--strict", "quiet please")
    assert r.exit_code == 10 and "voice is off" in r.output


def test_wait_times_out_with_124_and_reports_a_rejection_as_10(queue, tmp_path):
    (tmp_path / "0001-1-000001-aaaa.vibe").write_text("still queued")
    assert _say("--wait", "--timeout", "0.1", "hello").exit_code == 124
    (tmp_path / "0001-1-000001-aaaa.vibe").unlink()
    (tmp_path / "0001-1-000001-aaaa.poison").write_text("x")
    r = _say("--wait", "--timeout", "1", "hello")
    assert r.exit_code == 10 and "rejected" in r.output


def test_wait_returns_0_once_the_worker_is_done(queue):
    assert _say("--wait", "--timeout", "1", "hello").exit_code == 0     # nothing left in the queue dir


def test_a_broken_queue_is_status_10_not_a_traceback(monkeypatch):
    class Boom:
        def __init__(self):
            raise OSError("disk full")
    monkeypatch.setattr("nhj.queue_manager.QueueManager", Boom)
    monkeypatch.setenv("TTS_ENGINE", "audiocpp")
    r = _say("hello")
    assert r.exit_code == 10 and "could not queue" in r.output and "Traceback" not in r.output


def test_the_spoken_words_are_never_written_to_nhjs_logs(queue, monkeypatch):
    """`say` may carry anything the caller types, so it must not log the text (only the queue holds it)."""
    from nhj import resources
    logged = []
    real = resources.append_log
    monkeypatch.setattr(resources, "append_log", lambda path, line, *a, **k: (logged.append(line), real(path, line, *a, **k))[1])
    assert _say("the launch code is swordfish").exit_code == 0
    assert all("swordfish" not in line for line in logged)


# ---- muse's review of #100: a literal event must never speak other words -----------------------
def _audio_stubs(monkeypatch, *, engine="audiocpp", cache=None, ret=None, bank="/bank/generic.wav", synth=None):
    """Stub the audio adapter's collaborators; returns the list of clips handed to the mixer."""
    emitted = []
    monkeypatch.setenv("TTS_ENGINE", engine)
    monkeypatch.setattr(audio, "_audiocpp_cfg", lambda: {})
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: cache)
    monkeypatch.setattr(audio, "pick_return_clip", lambda: ret)
    monkeypatch.setattr(audio, "pick_bank_clip", lambda v, i: bank)
    monkeypatch.setattr(audio, "_normalize_voice_wav", lambda w: w)
    monkeypatch.setattr(audio, "_save_and_cache", lambda *a, **k: None)
    monkeypatch.setattr(audio, "_write_temp_wav", lambda w: "/tmp/exact.wav")
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: emitted.append(path))
    monkeypatch.setattr(audio, "synth_audiocpp", lambda *a, **k: synth)
    return emitted


@pytest.mark.parametrize("engine,synth", [("none", None), ("audiocpp", None)])
def test_literal_without_a_voice_is_not_spoken_instead_of_playing_a_generic_clip(monkeypatch, engine, synth):
    emitted = _audio_stubs(monkeypatch, engine=engine, synth=synth)
    assert audio.AudioAdapter().fire("ok", "Deploy 42 is done.", resolve_character("ok"), literal=True) is False
    assert emitted == []                                              # no stand-in words


def test_a_normal_event_still_falls_back_to_the_bundled_clip(monkeypatch):
    emitted = _audio_stubs(monkeypatch, engine="none")
    assert audio.AudioAdapter().fire("ok", "all done", resolve_character("ok")) is True
    assert emitted == ["/bank/generic.wav"]                           # unchanged behaviour for hooks and markers


def test_a_literal_event_may_use_a_cached_clip_of_the_exact_text(monkeypatch):
    emitted = _audio_stubs(monkeypatch, engine="none", cache="/cache/exact.wav")
    assert audio.AudioAdapter().fire("ok", "Deploy 42 is done.", resolve_character("ok"), literal=True) is True
    assert emitted == ["/cache/exact.wav"]


def test_literal_is_not_preceded_by_the_you_still_there_clip(monkeypatch):
    from nhj import inference_muzak
    monkeypatch.setattr(inference_muzak, "_any_busy", lambda: False)  # the music really paused
    emitted = _audio_stubs(monkeypatch, ret="/return/you-still-there.wav", synth=b"RIFF" + b"\0" * 2000)
    audio.AudioAdapter().fire("ok", "Deploy 42 is done.", resolve_character("ok"), literal=True)
    assert emitted == ["/tmp/exact.wav"]
    emitted.clear()
    audio.AudioAdapter().fire("ok", "all done", resolve_character("ok"))
    assert emitted[0] == "/return/you-still-there.wav"                # a normal completion keeps the cue


def test_a_literal_flag_with_no_message_behaves_like_a_bare_marker(monkeypatch):
    emitted = _audio_stubs(monkeypatch, engine="none")
    assert audio.AudioAdapter().fire("err", "", resolve_character("err"), literal=True) is True
    assert emitted == ["/bank/generic.wav"]


def test_say_never_accepts_more_than_the_speech_server_does():
    """A literal line has no fallback, so `say` must not queue text the TTS server would refuse.
    Read the server's cap from its source: importing it needs fastapi, which not every test env has."""
    import re
    from pathlib import Path
    src = (Path(say.__file__).parent / "tts_server.py").read_text()
    cap = int(re.search(r"^_TTS_MAX_CHARS\s*=\s*(\d+)", src, re.M).group(1))
    assert say.HARD_MAX_CHARS <= cap


# ---- codex review on #100: a cached exact clip is speakable with no engine --------------------
def test_literal_clip_cached_uses_the_same_key_as_fire(monkeypatch):
    seen = []
    monkeypatch.setattr(audio, "cache_hit", lambda voice, text, variant, tier: (seen.append((voice, text, variant, tier)) or "/c.wav"))
    ch = resolve_character("ok")
    assert audio.literal_clip_cached(ch, "ok", "Deploy 42 is done.") is True
    d = ch.delivery("ok", "Deploy 42 is done.", literal=True)
    assert seen == [(d.voice, "Deploy 42 is done.", d.band, d.model_tier)]
    from nhj.state import set_setting
    set_setting("voice_override", "whisper")                          # the global override changes the key, as in fire()
    seen.clear()
    audio.literal_clip_cached(ch, "ok", "Deploy 42 is done.")
    assert seen[0][0] == "whisper" and seen[0][2] == f"whisper:{d.band}"
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: None)
    assert audio.literal_clip_cached(ch, "ok", "never spoken before") is False


def test_no_engine_but_an_exact_cached_clip_is_speakable():
    import os
    os.environ["TTS_ENGINE"] = "none"
    try:
        assert say.can_speak("proj", cached=lambda: True).ok is True
        assert say.can_speak("proj", cached=lambda: False).ok is False
        assert say.can_speak("proj").ok is False
    finally:
        os.environ.pop("TTS_ENGINE", None)


def test_strict_succeeds_silently_when_the_exact_text_is_cached(queue, monkeypatch, capsys):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: "/cached/exact.wav")
    r = _say("--strict", "Deploy", "42", "is", "done")
    assert r.exit_code == 0 and "Deploy 42" not in r.output           # speakable: nothing echoed to stderr
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: None)
    assert _say("--strict", "something", "new").exit_code == 10        # not cached, no engine: still undeliverable
