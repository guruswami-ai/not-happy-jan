"""On-demand model-server supervisor (issue #27).

Spawned by :func:`nhj.servers.ensure` in on-demand mode. Runs the real server (the
TTS daemon or llama.cpp), then **terminates it — freeing the model RAM — once the
usage heartbeat goes stale** past ``NHJ_SERVER_IDLE``, then exits, so nothing stays
resident at rest. Run as ``python -m nhj.serverctl <tts|llm>``.
"""
from __future__ import annotations

import subprocess
import sys
import time

from nhj import servers


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m nhj.serverctl <tts|llm>", file=sys.stderr)
        return 2
    name = argv[0]
    if servers.is_up(name):
        return 0                                   # another supervisor already owns the port

    cmd = servers.server_command(name)
    if cmd is None:
        print(f"[nhj] serverctl: no command for {name!r} (is the model installed?)",
              file=sys.stderr)
        return 1

    servers.mark_used(name)                        # don't reap before the first request lands
    try:
        proc = subprocess.Popen(cmd)
    except OSError as e:                            # e.g. llama-server not on PATH
        print(f"[nhj] serverctl: failed to start {name}: {e}", file=sys.stderr)
        return 1
    idle = servers.idle_seconds()
    poll = min(15.0, max(2.0, idle / 20.0))
    try:
        while proc.poll() is None:
            if time.time() - servers.last_used(name) > idle:
                break                              # idle too long → reap below
            time.sleep(poll)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
