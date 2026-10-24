"""Run llama-server under a clearly-named ``NHJ LLM`` process.

llama.cpp's own binary would show as ``llama-server`` in Activity Monitor; this thin
supervisor sets the process title to ``NHJ LLM``, launches llama-server as a child,
forwards termination signals to it, and mirrors its exit code (so launchd KeepAlive
still restarts a crashed server). Invoke as::

    python -m nhj.llm_server <llama-server> [args...]
"""
from __future__ import annotations

import signal
import subprocess
import sys

from nhj.procname import set_title


def main() -> None:
    set_title("NHJ LLM")
    cmd = sys.argv[1:]
    if not cmd:
        sys.exit("usage: python -m nhj.llm_server <llama-server> [args...]")
    # Launchd has already opened llm.log on stderr. Rotate before the child
    # inherits that descriptor. A rename after Popen does not retarget llama.cpp.
    from nhj import resources
    resources.rotate_attached_log("llm")
    child = subprocess.Popen(cmd)

    def _forward(signum, _frame):
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _forward)
    sys.exit(child.wait())


if __name__ == "__main__":
    main()
