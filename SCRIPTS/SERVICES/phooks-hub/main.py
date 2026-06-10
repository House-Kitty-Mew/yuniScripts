#!/usr/bin/env python3
"""Phooks Hub – managed script for the YuniScripts engine.
Imports the canonical PhooksHub from engine.phooks and wraps it with
file logging, graceful shutdown, and SHUTDOWN_COMPLETE marker.
"""
import sys, traceback, time, signal
from pathlib import Path
from datetime import datetime

# ---- Logging ----------------------------------------------------------
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _log_file(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    log_path = LOG_DIR / "phooks_hub.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ---- Import canonical PhooksHub (eliminates duplication) -------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from engine.phooks import PhooksHub

from engine.ports import PHOOKS_HUB_PORT


def main():
    _log_file("===== Phooks Hub starting =====")

    # Instantiate the canonical hub with our file-logging callback
    hub = PhooksHub(
        port=PHOOKS_HUB_PORT,
        lan_broadcast=True,
        log_func=lambda msg, level: _log_file(msg, level),
    )
    hub.start(daemon=False)  # non-daemon so engine waits for clean shutdown

    # Register signal handlers for graceful shutdown
    def _shutdown(signum=None, frame=None):
        _log_file("Shutdown signal received")
        hub.stop()
        _log_file("Hub shut down.")
        print("SHUTDOWN_COMPLETE", flush=True)

    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    try:
        # Keep the script alive until signal
        while hub.running:
            time.sleep(1)
    except KeyboardInterrupt:
        _log_file("KeyboardInterrupt")
    except Exception as e:
        _log_file(f"Unhandled exception: {e}\n{traceback.format_exc()}", "FATAL")
    finally:
        _shutdown()
        _log_file("===== Phooks Hub exited =====")


if __name__ == "__main__":
    main()
