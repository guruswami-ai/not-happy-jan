# Public release checklist

This checklist is the publication gate for the first public alpha. Items are checked
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
- [ ] **GitHub private vulnerability reporting enabled** — *blocked until public (not
      available on the current private plan); applied to the fresh public repo by
      `scripts/go-public.sh`.*
- [ ] **Branch protection requires CI + secret-scan checks** — *blocked until public;
      applied to the fresh public repo by `scripts/go-public.sh` (requires `gitleaks` ·
      `package` · `test (3.10)` · `test (3.12)`).*
- [x] Code scanning (CodeQL): **explicitly deferred for the alpha.** Rationale — the
      codebase is a small pure-Python + shell surface with no web/server attack surface
      exposed by default (MCP/TTS bind loopback), secret leakage is already gated by the
      history-aware Gitleaks workflow, and Dependabot covers dependency CVEs. CodeQL will
      be enabled after the alpha (one-liner in `scripts/go-public.sh`'s closing notes).

## Transition order — clean public release (perform in this sequence)

We publish a **clean** repo rather than flipping the existing one: a visibility flip
would retroactively expose all commit history, every PR/review thread, and every issue,
including internal infrastructure references. **`bash scripts/go-public.sh --confirm`**
automates steps 1–4; steps 5–6 are manual.

1. **Archive** — rename the current repo to `guruswami-ai/not-happy-jan-private` (stays
   private; full history / PRs / issues preserved as the dev archive).
2. **Publish** — create a fresh public `guruswami-ai/not-happy-jan` from a **single
   squashed snapshot** of `main` (one "Initial public alpha release" commit, no legacy).
3. **Media** — re-create the `media` release on the public repo (assets copied from the
   archive) so the `nhj setup-media` / one-liner fetch keeps working.
4. **Controls** — enable private vulnerability reporting + branch protection on `main`
   (requires `gitleaks` · `package` · `test (3.10)` · `test (3.12)`; force-push/deletes
   blocked; `enforce_admins=true`).
5. Run the **literal public one-liner** on a clean Mac, then `nhj uninstall`:
   `curl -fsSL https://raw.githubusercontent.com/guruswami-ai/not-happy-jan/main/install.sh | bash`
   — tick the last installer-gate box.
6. Verify the `SECURITY.md` reporting link resolves; update any local clones' remotes.

CodeQL is deferred (above); re-enable post-alpha with the command in `go-public.sh`.

Do not publish until every non-flip item above is complete or explicitly removed through
a reviewed change explaining why it is not required.
