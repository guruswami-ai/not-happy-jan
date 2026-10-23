#!/bin/sh
# Codex CLI `notify` hook: Codex appends ONE JSON string (type, cwd, last-assistant-message, ...)
# as the last argv. Hand it to `nhj` on stdin so the vibe is labelled with the project folder.
#   ~/.codex/config.toml:   notify = ["/path/to/codex-notify.sh"]
printf '%s' "${1:-}" | nhj vibe ok -m "Codex turn done" >/dev/null 2>&1
exit 0
