"""On-demand model-server lifecycle (issue #27).

Two modes — ``NHJ_SERVERS`` env or ``nhj servers …`` (default ``persistent``):

- **persistent** — the TTS and LLM run as KeepAlive LaunchAgents, always warm
  (~3.2 GB resident, sub-second responses). Best for power machines.
- **on-demand** — nothing is resident at rest. The first dynamic/TTS vibe starts
  the server via a small supervisor (:mod:`nhj.serverctl`) that **reaps it after
  idle**, so the ~3 GB of model RAM is only held while NHJ is actually talking.
  Costs a one-off cold-start on the first vibe after an idle period.

A *server* is ``"tts"`` or ``"llm"``. The consumer (audio adapter / boganify)
calls :func:`ensure` before use; in on-demand mode that starts the supervisor if
the port is down and waits for it. :func:`mark_used` refreshes a heartbeat the
supervisor watches; when it goes stale past :func:`idle_seconds`, the supervisor
terminates the model server (freeing the RAM) and exits.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import fcntl                          # POSIX advisory locks; absent on Windows
except ImportError:                       # pragma: no cover - non-POSIX
    fcntl = None

from nhj import resources

DEFAULT_IDLE = 600          # seconds a server may sit idle before it's reaped

_DEFAULT_PORTS = {"tts": 9992, "llm": 9991}
_PORT_ENV = {"tts": "NHJ_TTS_PORT", "llm": "NHJ_LLM_PORT"}
_URL_ENV = {"tts": "NHJ_TTS_URL", "llm": "NHJ_DYNAMIC_BASE_URL"}


def _port(name: str) -> int:
    """Configured port for *name*: explicit ``NHJ_*_PORT`` wins, else the port
    parsed from the installer-written service URL (``NHJ_TTS_URL`` /
    ``NHJ_DYNAMIC_BASE_URL``), else the default. Read live so it honours whatever
    ``install-tts`` / ``install-model --port`` wrote to ``.env``."""
    explicit = os.getenv(_PORT_ENV[name], "")
    if explicit.isdigit():
        return int(explicit)
    m = re.search(r":(\d{2,5})\b", os.getenv(_URL_ENV[name], "") or "")
    return int(m.group(1)) if m else _DEFAULT_PORTS[name]


def mode() -> str:
    """'persistent' (default) or 'on-demand' — env wins, else the saved setting."""
    m = os.getenv("NHJ_SERVERS")
    if not m:
        try:
            from nhj.state import get_setting
            m = get_setting("servers", "persistent")
        except Exception:
            m = "persistent"
    return str(m or "persistent").strip().lower()


def idle_seconds() -> int:
    try:
        return max(30, int(os.getenv("NHJ_SERVER_IDLE", str(DEFAULT_IDLE))))
    except ValueError:
        return DEFAULT_IDLE


def _heartbeat(name: str) -> Path:
    return resources.state_dir() / f"{name}.heartbeat"


def mark_used(name: str) -> None:
    """Record that *name* was just used, so the supervisor keeps it alive.

    Atomic (temp + replace) so the supervisor never reads a half-written file."""
    try:
        hb = _heartbeat(name)
        tmp = hb.with_name(f"{hb.name}.tmp-{os.getpid()}")
        tmp.write_text(str(time.time()))
        os.replace(tmp, hb)
    except OSError:
        pass


def last_used(name: str) -> float:
    try:
        return float(_heartbeat(name).read_text().strip())
    except (OSError, ValueError):
        return 0.0


def is_managed_llm(base_url: str) -> bool:
    """True if *base_url* points at NHJ's own local LLM server (so on-demand may manage
    it). Bring-your-own endpoints (Ollama, a cloud API, …) are the user's to run."""
    return (f":{_port('llm')}" in base_url) and ("127.0.0.1" in base_url or "localhost" in base_url)


def is_up(name: str, host: str = "127.0.0.1") -> bool:
    """True if something is listening on *name*'s port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex((host, _port(name))) == 0
    except OSError:
        return False


def is_ready(name: str, host: str = "127.0.0.1") -> bool:
    """True when the server accepts requests, not merely TCP connections."""
    if not is_up(name, host):
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(
            f"http://{host}:{_port(name)}/health", timeout=0.5
        ) as response:
            return response.status == 200
    except Exception:
        return False


def ensure(name: str, wait: float = 45.0) -> bool:
    """Make sure server *name* is reachable; refresh its heartbeat.

    In on-demand mode, start the supervisor (under a lock, so concurrent callers
    don't double-spawn) and wait until the port answers. In persistent mode this
    just reports whether the LaunchAgent-managed server is up.
    """
    mark_used(name)
    if is_ready(name):
        return True
    if mode() != "on-demand":
        return False                         # persistent: the LaunchAgent owns the lifecycle

    if fcntl is None:                        # no advisory locks (Windows) — spawn without serialising
        if not is_ready(name):
            _spawn_supervisor(name)
            _wait_up(name, wait)
        return is_ready(name)

    lock_path = resources.state_dir() / f"{name}.spawn.lock"
    try:
        lock = open(lock_path, "w")
    except OSError:
        return is_ready(name)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)     # serialise spawns across callers
        if not is_ready(name):
            _spawn_supervisor(name)
            _wait_up(name, wait)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return is_ready(name)


def _wait_up(name: str, wait: float) -> None:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and not is_ready(name):
        time.sleep(0.3)


def _spawn_supervisor(name: str) -> None:
    """Launch the detached supervisor that runs + idle-reaps the model server.

    A failing log file must never prevent the spawn — fall back to DEVNULL — and
    the log directory may not exist yet on a fresh install, so create it."""
    out = subprocess.DEVNULL
    try:
        logf = Path(resources.log_file(f"{name}-server.log"))
        logf.parent.mkdir(parents=True, exist_ok=True)
        out = open(logf, "ab")
    except OSError:
        out = subprocess.DEVNULL
    try:
        subprocess.Popen([sys.executable, "-m", "nhj.serverctl", name],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
    except OSError:
        pass
    finally:
        if out is not subprocess.DEVNULL:
            try:
                out.close()
            except OSError:
                pass


def server_command(name: str) -> list[str] | None:
    """The command the supervisor runs for *name*, or None if it can't be built."""
    if name == "tts":
        return [sys.executable, "-m", "nhj.tts_server",
                "--host", "127.0.0.1", "--port", str(_port("tts"))]
    if name == "llm":
        llama = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
        gguf = next(iter(sorted(resources.llm_model_dir(create=False).glob("*.gguf"))), None)
        if not Path(llama).exists() or gguf is None:
            return None
        return [llama, "-m", str(gguf), "--alias", "ocker-bogan-nano",
                "--host", "127.0.0.1", "--port", str(_port("llm")),
                "-c", "2048", "--no-webui"]
    return None
