# Public release checklist

This checklist was the publication gate for the first public alpha. **Released as a
public alpha on 2026-06-09** (clean fresh repo from a squashed snapshot; full dev
history retained privately at `guruswami-ai/not-happy-jan-private`). Items are checked
when verified, with evidence noted inline.

## Automated gates — ✅ green on `main`

- [x] CI passes on supported Python versions (3.10, 3.12) on Apple Silicon macOS.
- [x] The complete pytest suite passes.
- [x] The wheel and source distribution build successfully.
- [x] The installed wheel resolves bundled config, hook, skill, character, **and the
      pre-rendered voice bank** without a source checkout. *(test_voice_bank, test_packaging)*
- [x] The history-aware Gitleaks workflow passes.
- [x] Shell syntax checks pass for the installer and media builder.
- [x] The built sdist contains no datasets, model weights, generated media, legacy
      service templates, `.DS_Store`, or AppleDouble files.

## Installer gates — ✅ verified on clean machines

Evidence: a from-scratch **`--minimal`** install + uninstall on a clean Apple Silicon
Mac (`shazza`), and a full uninstall → **default/`--full`** install on `Om`.

- [x] The documented install path succeeds on a clean supported Mac — source download
      + `uv` bootstrap + managed Python, hooks/markers/skill/MCP registered, bundled bank
      audible. *(The literal public `curl … | bash` one-liner is the one remaining check
      that can only run after the visibility flip — the script it fetches is byte-identical
      to the verified source path.)*
- [x] Default, full, and minimal profiles match their documentation — reconciled across
      README, install-profiles, minimum-specs (this gate's PR); on `Om` the full profile
      installed + ran live TTS (Qwen3), the `ocker-bogan-nano` LLM, hold music, and the
      persistent `NHJ TTS` / `NHJ LLM` / `NHJ MCP` LaunchAgents.
- [x] A failed required installation step returns a non-zero status. *(test_installer)*
- [x] MCP network exposure remains opt-in and defaults to loopback (`127.0.0.1`).
- [x] Upgrade, reinstall, and uninstall behavior is documented and tested — `nhj uninstall`
      stops services + removes everything user-context (no sudo), verified end-to-end on
      `shazza`. *(test_uninstall, README Uninstall)*

## Manual publication gates

- [x] Issue #52 complete: per-source provenance + licensing documented
      (`docs/media-provenance.md`); the build allow-list (`scripts/media-manifest.txt`)
      no longer carries stale licensing assertions.
- [x] The `media` release assets are published with GitHub SHA-256 digests; the bundle
      validator (`scripts/validate_media_bundle.py`) passes.
- [x] Repository description, topics, MIT license, `SECURITY.md`, `CONTRIBUTING.md` present.
- [x] **GitHub private vulnerability reporting enabled** on the public repo.
- [x] **Branch protection on `main`** requires `gitleaks` · `package` · `test (3.10)` ·
      `test (3.12)` · `Analyze (python)` · `Analyze (actions)`; `enforce_admins=true`;
      force-push/deletes blocked.
- [x] Code scanning (CodeQL): **enabled and required** — `.github/workflows/codeql.yml`
      analyzes `python` + `actions` (build-mode none, `security-and-quality` queries) on
      push to `main`, PRs, and weekly; first scans returned **0 alerts**; the `Analyze`
      checks are required to merge (see branch protection above).

## Transition order — clean public release (✅ completed 2026-06-09)

We published a **clean** repo rather than flipping the existing one (a flip would have
exposed all history, PRs, issues, and the internal infra references in them). Steps 1–4
were run by `bash scripts/go-public.sh --confirm`:

1. ✅ **Archive** — renamed the original repo to `guruswami-ai/not-happy-jan-private`
   (private; full history / PRs / issues preserved).
2. ✅ **Publish** — fresh public `guruswami-ai/not-happy-jan` from a single squashed
   "Initial public alpha release" commit (no legacy, no infra leak).
3. ✅ **Media** — `media` release re-created on the public repo.
4. ✅ **Controls** — private vulnerability reporting + branch protection (now also
   requiring the CodeQL `Analyze` checks).

Remaining post-publish:

- [ ] Run the **literal public one-liner** on a clean Mac, then `nhj uninstall`:
   `curl -fsSL https://raw.githubusercontent.com/guruswami-ai/not-happy-jan/main/install.sh | bash`
   (the URL resolves; the script is byte-identical to the verified source path).
- [ ] Verify the `SECURITY.md` reporting link resolves; update any local clones' remotes.
