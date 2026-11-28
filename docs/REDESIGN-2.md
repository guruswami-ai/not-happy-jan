# NHJ v2 — clean-sheet specification (draft, not normative)

**Status:** PROPOSED. For maintainer review. Nothing here changes behaviour until it lands in
`SPEC.md` with tests, which remains the normative document.

**Two scope decisions taken by the maintainer on 2026-11-27,** reflected throughout: **Windows is not a
requirement** (ADR-4 shrinks to a hygiene rule, and v2 matches `SPEC.md` §1 rather than extending it),
and **an always-on `nhjd` daemon is acceptable** (ADR-1, which changes the idle budgets in §1 and buys
the library-rescan behaviour described there).

**Two more on 2026-11-28:** the wire protocol supports **exactly one version** (ADR-2), and the voice
ladder is **instrumented in phase 1** next to the mixer (ADR-10), so the latency the user actually hears
is measured before anything else in this document gets built.

**Baseline measured on `main` at `66a6322`**, 2026-11-27: 11,029 lines of Python in 49 modules,
8,606 lines of tests in 55 files. Labels used below: **[verified]** measured here, **[derived]**
carried from `SPEC.md` §9, **[unresolved]** cannot be answered yet.

**Update at publication, 2026-11-28:** §5 step 1 has landed (PR #111, §5.1), so the instruments ADR-10
asks for now exist while the field numbers they produce mostly do not. Read §1 as the pre-phase-1
baseline it was measured as — the voice and callback rows have counters behind them today, the figures in
that column are what shipping code produced before any of them existed.

---

## 0. What v2 does not reopen

These are already tight, and a rewrite that touches them buys regression risk, not clarity.

| Carried over | Why |
|---|---|
| Purpose and the never-block-the-agent invariant (§1) | The whole product rests on it |
| `VibeEvent` field set and validation rules (§2) | Well-formed; becomes a wire-versioned schema instead of an in-process contract |
| Marker syntax, backtick rule, fire-once digest (§3) | Encoded in 8.6k lines of tests; re-deriving it loses audit findings |
| Degrade-don't-fail, and never-silently (§8) | Correct philosophy, keep the words |
| Budgets (§9) | The numbers stay, but become measured SLIs with CI gates, not prose. The voice ones are unmeasured today, so phase 1 is allowed to find them wrong and §6 records what happens then |

## 1. Efficiency, defined and measured before it is targeted

"Optimal efficiency" has to name a quantity. Today's real numbers, so the target is not a wish:

| Quantity | Measured now [verified] | v2 target | Why that number |
|---|---|---|---|
| `import nhj.hook` | 19 ms | ≤ 8 ms | Hook runs per prompt; it is the only per-event interpreter cost we control |
| `nhj --version` cold start | 238 ms | ≤ 90 ms | CLI is interactive; typer+rich dominate, not our code |
| Hook → audible feedback | §9 ceiling: p95 ≤ 500 ms | unchanged | Already met; the ceiling is not a target you can hear |
| Voice render, bank/chime rung | not measured (§9 *claims* ≤ 300 ms) | p95 ≤ 300 ms, measured | ADR-10. An unmeasured promise is not a budget |
| Voice render, live-synthesis rung | not measured (§9 *claims* ≤ 2 s warm) | p95 ≤ 2 s warm, per engine | Split by engine — `qwen`, `audiocpp`, OS voice, `piper` are not one number |
| Voice ladder hit rate | not measured | reported, no target yet | The only figure that says whether a ≤300-clip cache earns its disk |
| TTS upstream failures | log-scraped at baseline: `audiocpp TTS error: 500` in `worker.log` | error counter per engine | Finding these today means grepping a log by hand |
| Audio callback budget | 21 ms per 1024-frame block [verified] | 0 underruns, measured | No counter existed at baseline; the one ADR-5 asks for shipped in §5.1 |
| Mixer + worker idle RSS | §9 target < 150 MB | ≤ 120 MB, one process | One daemon replaces two resident roles |
| Daemon start | not measured | ≤ 400 ms | Must stay far below first-need latency |
| Idle cost, resident | n/a — mixer self-exits after 600 s today | < 0.5 % of one core, ≤ 120 MB RSS | The daemon no longer exits, so it must earn staying |
| Event → wake latency | n/a | p95 ≤ 5 ms | A socket wake, not a poll interval |
| Cross-process mutable state | 3 JSON files + 2 dirs + heartbeats | 1 SQLite DB | Correctness, not speed |
| Engineering efficiency | 6 required checks coupled to CI job names | conformance map, req→test | A spec nobody can check has no teeth |

The uncomfortable finding worth stating plainly: **the per-event path is already fast** (19 ms), so a
rewrite justified on runtime speed is not supported by evidence. What *is* measurably wrong is state
management — a correctness argument, not a speed one. What is worse than wrong in the audible paths is
that the four voice rows above are the latency a user actually judges, and three of them have no number
at all; the one audio path that has been measured closely enough to quote has no recorded faults.
ADR-10 exists to break that inversion before anything gets rewritten.

## 2. Decisions

Each records the options considered, the choice, and the consequence — the point of a spec is to
close decisions, not to display them.

### ADR-1 — one daemon, one thin client

Today: hook (transient), worker, mixer/controller, MCP server, plus supervised model servers,
communicating through JSON files guarded by `flock` + temp + rename — a bus nobody designed, and
`SPEC.md` §6 already concedes "several files violate this today (audit B1/B2)".

v2: **`nhjd`**, one resident process owning queue, routing, adapters, mixer and library. In the hook
path, a **stdlib-only client** (≤300 lines, no third-party imports, no `nhj.*` package import) that
writes one frame to a socket and returns. The MCP server stays, as a thin adapter onto the same
protocol, so every agent-facing surface is unchanged.

Consequence: the audit-class concurrency bugs stop being possible, because there is one writer.
The hook stays a separate process (it must be spawnable by an arbitrary CLI), which is why its import
cost is a stated budget rather than something we delete.

**Always-on, by maintainer decision.** Today the controller self-exits after 600 s idle to keep the
footprint near zero; v2 keeps `nhjd` resident instead. That has to be paid for — hence the idle CPU and
RSS rows in §1 — and installed as a user service: systemd user unit with lingering on Linux, LaunchAgent
on macOS, `nhj disable` as the explicit off switch, and `nhj uninstall` removing the unit, the DB and
the media dir.

What it buys is concrete, because we hit the failure this week: the mixer snapshots its playlist when it
starts, so 102 freshly imported tracks sat in the pool unheard until the controller happened to restart,
and the only lever was `nhj muzak off && nhj muzak on`. A resident daemon owning the library table
rescans on `nhj library import` and on `nhj reload`, so "I added a song" and "the bed can play it" stop
being two separate events. It also retires `controller_pid`, the 1800 s eviction dance in `music.json`,
and most of the PID-liveness argument in §6.1.

### ADR-2 — transport: length-prefixed JSON Lines over one local stream socket

Rejected: loopback HTTP (an HTTP server in the hook path costs more than the 19 ms we are trying to
shed, and needs a port); gRPC (a codegen and dependency tax for one local pipe); SQLite-as-queue
(great as a store, wrong as a notification channel — it has no poll primitive that beats a socket).

Chosen framing: 4-byte big-endian length + one JSON object. `HELLO` carries `proto`, `role`,
`nhj_version`; the daemon replies `capabilities`. Unknown fields are rejected with an `error` frame,
matching §2's "never silently forwarded".

**Exactly one version is supported** (maintainer decision, 2026-11-28). The `proto` field exists so a
second version *can* be introduced deliberately, not so that two may coexist now: this is one host, one
install, and the upgrade is atomic, so negotiation would be a permanent cost bought against a problem
that does not exist. A second version enters only when a client and a daemon demonstrably outlive each
other across an upgrade — a packaged host that updates independently, or a shared LAN install.

A mismatch is a hard error, with one deliberate asymmetry: loud in `nhj doctor` and `nhj status`, silent
and successful in the hook path, because a stale hook MUST NOT fail an agent session (§8).

`Done when`: a test sends `proto = 999` and asserts the daemon answers `error`, a hook invocation still
exits 0, and `nhj doctor` names the version gap in one line.

Address: `AF_UNIX` at `$XDG_RUNTIME_DIR/nhj/sock`, falling back to the state dir when
`XDG_RUNTIME_DIR` is unset — which it always is under a macOS LaunchAgent, so the fallback is the normal
path there, not an edge case. One framing, one listener kind.

### ADR-3 — state is SQLite, WAL, schema-versioned

`state.json`, `music.json`, `hook-seen.json`, the `.vibe`/`.proc` queue and the audio spool become
tables: `events`, `event_claims`, `sessions_busy`, `seen_markers`, `assets`, `dials`, `settings`,
`voice_gate`, `secret_hits`. `user_version` drives migrations; `nhj doctor` refuses to run a mixed
version.

`Done when`: a crash-injection test SIGKILLs the daemon at every commit boundary across 200 events and
after restart there are **no lost events, no duplicated plays, and no row claimed by a dead owner** —
today that invariant is argued from the lease rules in §6.1, and it deserves to be executed.

Consequence: the `.poison`/requeue/lease machinery shrinks to `SELECT ... FOR UPDATE`-equivalent claim
plus one retry column, and log rotation of spool files disappears.

### ADR-4 — platform hygiene, not a port

Windows is **out of scope** (maintainer decision, 2026-11-27). v2 stays inside `SPEC.md` §1 — macOS
(Apple Silicon) and Linux, Windows explicitly excluded — rather than extending it.

That deletes most of one phase (§5 step 6 shrinks to the gate below): no Win32 job objects, no Task
Scheduler backend, no `windows-latest` CI job, and no branch-protection context churn to accompany one.
The port would have been smaller than it
looks — [verified] `fcntl` in 6 modules, `signal` in 5, `select` in 1,
`os.setsid`/`killpg`/`AF_UNIX` at 4 call sites, service registration confined to `cli.py`, and no
`termios`, `pwd`, `grp`, `resource` or `ctypes` anywhere — but small is not the same as worth it, and
WASAPI shared-mode latency is unmeasurable here without a machine to measure it on.

What survives is the part that costs nothing and prevents rot:

| Rule | Enforcement |
|---|---|
| OS-facing calls live in `nhj/platform/` (`paths`, `service`, `lock`, `audioctl`) | Build-failing grep gate: no `fcntl`, `signal.SIG*`, `os.setsid`/`killpg` or `AF_UNIX` outside it. Today those sit scattered across 6 + 5 + 1 modules and 4 call sites |
| Paths come from `platform.paths`, never string-built | Test asserts no literal `~/.config/nhj` / `~/.local/state/...` outside `platform/` and the documented dev fallback |
| Service install is one module per platform | systemd user unit + lingering, and a LaunchAgent plist, generated from one description so `status` and `doctor` can reason about either |

The gate is the whole decision. Without it, POSIX creeps back into a domain module within a week and the
abstraction becomes a fiction — which is how `SPEC.md` §1's "cannot import there today: unconditional
`fcntl`" note came to describe six modules rather than one.

### ADR-5 — audio: specify the engine boundary, do not pre-decide its language

[verified] The mixer is a PortAudio callback at `BLOCK = 1024` (~21 ms) with numpy summing and an
explicit "no I/O in the callback" boundary. There is no underrun counter, and the only audio faults in
`worker.log` are `audiocpp` TTS 500s — upstream HTTP failures, not glitches. So Python's sufficiency
here is currently an opinion, not a measurement.

v2 specifies the engine as an interface with a hard contract: `open(device, rate, blocksize)`,
`submit(clip)`, `set_duck(env)`, `stats() → {underruns, short_frames, max_callback_us}`, and a test
double driven by an injected clock so mixing is deterministic without a sound card. The Python/numpy
engine is the first implementation. A Rust `cpal` engine is allowed to drop in **if and only if** the
counter shows underruns in the field. Rewriting the application around audio was, and remains,
unjustified.

### ADR-6 — the music library is data, not a directory tree

[verified] Tags currently come from `<genre>/<lang>/` folder position, the pool and the library are
hard links to one inode, and the scanner's cross-directory dedupe compares `resolve()`d paths — which
does not collapse hard links. Pointing at both roots counted **629 tracks instead of 354**. Folder
position as a database is the root cause; my `.env` workaround dodged the symptom.

v2: an `assets` table `(id, path, dev, ino, sha256, duration_s, lufs, true_peak, genre, lang,
song_id, source_url, imported_at)` with a unique index on `(dev, ino)` — hard links become impossible
to double-count by construction — and a `pools` view. Import becomes `nhj library import <file>
--genre --lang`, which is also where the `muzak-factory` tooling (today living outside the repo, with
its own JSON ledger and TSV) finally gets a home and a schema.

Loudness spec moves in with it: `-15 LUFS ±1`, true peak ≤ `-1.0 dBTP`, verified on the file at import
and re-verifiable with `nhj library verify`, because the ledger-vs-file distinction cost a whole
debugging session (`tighten_peaks.py` could only fix what its own ledger already knew was wrong).

### ADR-7 — config: one format, reload without restart

TOML everywhere (`.env` and `config.yaml` both exist today, and the live deploy has a comment in
`.env` explaining a constraint the scanner cannot see). Keep the env registry idea from §5 — it is
good — and finish it: every read goes through the typed registry, which is what makes a reload safe.
`nhj reload` becomes a protocol command; `SIGHUP` stays as the POSIX spelling. Closes §6's open item
(config frozen at process start).

Secrets keep today's model deliberately: **loopback needs no credential**, LAN exposure is opt-in with
the existing optional shared secret. v2 adds no token plumbing, and `nhj doctor` prints one blunt line
naming whatever exposure is currently active.

### ADR-8 — Python and packaging

Floor `>=3.11` is what the dependency tree forced in #110 [verified]. Moving to `>=3.12` is
**[unresolved]** — worth one resolution attempt in the first phase, and only that, since the last floor
change cost a CI matrix edit plus a branch-protection context swap to `test (3.12)`.

Distribution is the packaging goal, because that is the actual user-facing weakness: today a host needs
Python ≥3.11, uv, a venv, and system PortAudio — CI has to `apt-get install libportaudio2` to run the
suite, which is a preview of every user's first failure. `Done when`: on a clean Ubuntu box and a clean Apple Silicon macOS box,
`uv tool install nhj && nhj install && nhj doctor` reaches green with no system package installed by
hand.

### ADR-9 — model servers stay out of process

LLM and TTS servers remain separate supervised processes. That decision was right and is not reopened;
what changes is that their health is answered over the protocol socket rather than inferred from
heartbeat files.

### ADR-10 — one measurement surface, both audible paths instrumented, in phase 1

Every budget in §1 and in `SPEC.md` §9 is answered by one command, `nhj stats`, over the protocol — not
by grepping logs, and not by a unit test asserting a constant someone typed.

**Voice is instrumented in the same phase as audio, not later** (maintainer decision, 2026-11-28). The
asymmetry is worth stating because it is the mistake this document exists to avoid: §9 promises
*bank/chime ≤ 300 ms* and *live voice ≤ 2 s warm* and **nothing measured either** at baseline, while the
mixer callback — the one path with no recorded fault, whose only log evidence is upstream `audiocpp` 500s
— is what a redesign would naturally reach for first. Measuring the safe path because it is the easy path is
exactly the failure mode to design out.

Reported per rung of the §7.1 ladder: count, p50/p95 wall time, bytes out, error count — split by engine
(`qwen`, `audiocpp`, OS voice, `piper`) and by outcome (bank hit, cache hit, synthesized, fell back).
Plus ladder hit rate, and the mixer's `underruns` / `short_frames` / `max_callback_us` from ADR-5.

`Done when`: the `audiocpp` 500s that today exist only as `worker.log` lines surface as an error counter
on the synthesis rung; `nhj stats` answers both paths in one call; CI fails a callback that cannot keep up
with its block; and a real host can fail a >10 % regression once `--save-baseline` has armed it. The gate
arms on a measured baseline rather than on §9's unmeasured guesses — otherwise the first run on a new host
fails for writing down the truth.

Consequence, stated plainly: instrumenting the ladder may show the 2 s figure is simply wrong for the CPU
tier on a machine without a GPU. The honest response is to amend §9, not to declare the code broken
against a number nobody has ever measured.

## 3. What v2 will not buy

Stated so the review can be honest about the payoff.

- **Not perceptibly faster feedback.** The hook path is 19 ms of imports against a 500 ms budget it
  already meets. The only human-detectable gain in the whole project is removing cold-start variance
  from CLI invocations, worth a few tens of milliseconds.
- **Not a Rust rewrite, and not a port either.** With Windows out of scope there is no platform
  argument left at all, and nothing measured argues for changing language on performance grounds (§1).
  What remains of platform work is a hygiene gate that costs an afternoon.
- **Not new capability.** v2 ships no feature that v1 cannot do. Its output is fewer moving parts,
  one state store, two supported platforms kept POSIX-legible by a gate, and budgets that are measured
  rather than asserted.
- **Not a licence to discard the tests.** 8,606 test lines encode the audit findings — fire-once
  digests, lease reclaim, censor, secret guard, backtick rule, tail-bounded transcript reads. A rewrite
  that re-derives that behaviour instead of porting it as fixtures is a machine for regressing
  2026's bug finds. **This is the main risk of the project, and it is a process risk, not a technical
  one.**
- **Not free.** Order-of-magnitude: the state-store consolidation and daemon consolidation are the bulk
  of the cost, and neither shows up in a demo.

## 4. Shape

`cli.py` at 1,791 lines is the real centre of gravity, and 49 flat modules encode no layering. v2
makes the layering a tested constraint rather than a diagram:

```
nhj/
  wire/       protocol frames, versions, codegen'd schema from nhj/event   (no I/O)
  platform/   paths.py, service.py, lock.py, audioctl.py                   (the only OS-facing layer)
  store/      SQLite schema, migrations, claims, assets                    (the only persistence)
  core/       routing, gating, queue, ducking envelope                     (pure, clock injected)
  engines/    audio/ (portaudio impl), voice/ (ladder), displays/, haptic/
  daemon/     assembly, supervision, protocol server
  client/     stdlib-only hook client, ≤300 lines, imports nothing from nhj
  cli/        typer surface, split from today's single module
```

`Done when`: a test asserts no `nhj.client` import outside `client/`, no third-party import inside
`client/`, no `fcntl`/`signal`/`setsid` outside `platform/`, and no `open(` outside `store/` and
`platform/` for state paths. Import-linter or a 40-line test; the point is that it fails the build.

Every module boundary above is one clock-injection or fake-audio-double seam, which is what makes the
`core` budget checks runnable in CI without a sound card.

## 5. Build order (strangler, each step independently shippable and revertible)

| # | Step | Independent value | Done when |
|---|---|---|---|
| 1 | `nhj stats`: mixer underruns and callback time, **and** per-rung voice timing, hit rate and errors (ADR-5, ADR-10) | Turns ADR-5 from opinion into data, and finally measures the latency the product is judged on | Both paths reported by one command; the `audiocpp` 500s appear as a counter, not a log grep; CI gates the callback against its block deadline and `--check` gates a real host (§5.1) |
| 2 | Extract stdlib hook client | Removes ~11 ms of the per-prompt path, zero behaviour change | Hook suite passes against both old and new client |
| 3 | SQLite behind today's file APIs, dual-write | Kills the flock/temp/rename class of bug | Crash-injection invariant above passes; files deleted after a release |
| 4 | `nhjd` consolidation + service install (unit/LaunchAgent, lingering, `nhj disable`) | One writer; budgets measurable; a new song is playable without a restart | §9 budgets emitted as counters and gated in CI; `nhj library import` is audible on the next event, no `muzak off && on` |
| 5 | `assets` table; import tooling moves in-repo | Ends the hard-link double count for good | `nhj library verify` reproduces 275/275 inside spec from files, not from a ledger |
| 6 | `nhj/platform/` extraction + the grep gate from ADR-4 | Keeps POSIX where it can be reasoned about | Gate fails the build on a deliberately planted `fcntl` in a domain module |

Six phases still, but the one that was Windows is now only the grep gate — the half of that phase worth
keeping on its own merits. Dropping the port removed no other step, which is the measure of how much
Windows actually cost this design.

Step 1 is first on purpose. The mixer counters are roughly ten lines; the voice rungs are one decorator
around the ladder and one table to read out of. Both ship before a line of the rewrite, so every later
phase either has to beat a number that already exists or can be dropped without anyone having to guess
what it cost. It also retires the weakest argument in this document: without phase 1, §2's audio section
is a rewrite justified by a hunch about a path that has no recorded faults.

### 5.1 Step 1 landed (2026-11-28, PR #111), and one wording of its gate was wrong

`nhj stats` measures both paths: the mixer's own callback timing, deadline misses and bed underruns, and one
record per spoken vibe — rung, engine, ladder wall time, synthesis round trip alone, clip size, failure
reason. Measured on a full synthetic mix (bed + voice + fx + ambient + compressor, no audio device,
400 blocks, both Python legs): **callback p50 219–232 µs, p95 237–261 µs, max 528–610 µs** against a
21.3 ms block — about 1 % of the deadline. ADR-5's
question is answered in the direction it expected: nothing here asks for another language, and §6 item 3 is
now a thing a real session can report rather than a thing to argue about.

Where those numbers come from matters, so it is stated: they are a full synthetic mix in CI plus a
development checkout. #111 was unmerged when this page was published, and the deploy script updates a host
from `origin/main`, so a running NHJ install has no `stats` command until the merge and the update that
follows it. §6 items 2 and 3 stay open in the only sense that counts — nothing has been measured on the
machine that actually serves voice and audio.

The wording that was wrong is this table's own *Done when*. "CI fails a >10 % regression" cannot be met with
a stored ratio: a regression ratio on per-callback microseconds flakes on shared runners, because the number
is real but the machine it was taken on is not. What CI can hold is the invariant that actually matters — a
**deadline**: the callback must finish inside the block it is filling. So `tests/test_mixer_stats.py` drives
the real callback and gates p95 under 25 % of a block, and the ratio gate survived where a 10 % comparison
means something: `nhj stats --save-baseline` then `--check`, per host, armed on that host's own numbers.

Two findings the instrument produced on its own:

- **A repeated message is not a repeated line.** A non-literal vibe re-picks its phrase per call, so the
  same words can be two cache keys and two syntheses. The hit rate is not a constant to assume, and it is
  now the number that says whether the clip cache earns its disk.
- **An unmeasured surface hides missing commands.** Writing the `stats` command briefly swallowed
  `@cli.command("dynamic-test")`, which removed a command from the product with no error anywhere and no
  test that read `--help`. A guard now asserts that every command-shaped function is registered.

**Then the review round found five more, one of them serious.** Merging the branch as reviewed would have
shipped a fault that stops the product's only audible output:

- the flag counter did `int(status)`. sounddevice passes `None` on a clean block and a `CallbackFlags`
  otherwise, and that class has neither `__int__` nor `__index__`, so the first real xrun raised
  `TypeError` inside the callback — where sounddevice stops the stream and never calls again, while
  `StreamKeeper`'s `live` flag stayed set and no one restarted it.
- `percentile` rounded its rank instead of rounding up, so a small window could discard its slowest
  observation and `--check` passed a host at 3000 ms against a 2 s budget.
- `--save-baseline` swallowed a failed write and still reported the gate armed.
- `--json --check` appended human verdict lines to the JSON document on stdout.
- the engine table filtered on the spoken rungs, so an engine failing every call had no row — the opposite
  of what ADR-10's `Done when` promises the `audiocpp` 500s buy.

CodeQL added two `Empty except` alerts on the way. All seven are fixed, and each fix carries a test that
was **mutation-checked**: put the old line back, the named test fails. The reason is the one lesson worth
keeping from this — the test that hid the first finding handed the callback the integer `4`, because the
code read it as a bitmask. A stub shaped like whatever the code expects proves nothing about the shape of
the thing on the other end of it, and eleven green checks plus 797 passing tests did not get any closer.

## 6. Open questions

**Resolved by the maintainer on 2026-11-27:** Windows is not a requirement (ADR-4 reduced to the
platform gate and per-platform service install); an always-on `nhjd` is acceptable, so §1's idle budgets
replace the 600 s self-exit and the library-rescans-without-restart behaviour becomes a requirement
rather than a side effect.

**Resolved by the maintainer on 2026-11-28:** the wire protocol carries **exactly one** supported version
(ADR-2) — a number the daemon prints and refuses, not a range it bridges, because nothing ships a second
version until something outside this machine is built against the first. Voice is instrumented **in phase
1**, not deferred (ADR-10), so phase 1 is one measurement surface over both audible paths and §9's
promises get checked before anyone re-promises them.

Three remain, and unlike the four above each has an answer you can go and get rather than make:

1. **Can the floor be `>=3.12`?** ADR-8 keeps `>=3.11` because that is what the dependency tree forced in
   #110 [verified]. The test is a `uv lock` against a 3.12 floor; the cost if it resolves is one CI matrix
   edit plus the branch-protection context swap to `test (3.12)`, which #109 proved is a same-change
   operation and nothing worse.
2. **Do §9's voice budgets hold on the machine that actually serves them?** `bank/chime ≤ 300 ms` and
   `live voice ≤ 2 s warm` were written before any rung had ever been timed. Phase 1 produces the numbers;
   if p95 misses at the engine that wins the ladder most often, §9 is amended to what the hardware does
   and the budget becomes real instead of aspirational.
3. **Does the interpreter ever delay the audio callback on a real host?** `max_callback_us` now exists, and
   CI's synthetic full mix answers the pessimistic version: p95 237–261 µs against a 21.3 ms block with bed,
   voice, fx, ambient and the compressor all sounding at once. What is still unknown is the machine that also
   hosts a TTS model. If `nhj stats` shows callback time flat while TTS round trips and library rescans run in
   the same process, Python's sufficiency argument is closed for good. If it spikes in correlation with that
   work, the honest reading is GIL contention in the mixer process — and the options are pinning the mixer
   thread, splitting the mixer into its own process, or watching free-threaded CPython, none of which should
   be designed for before the counter has been read on the machine that actually runs NHJ.
