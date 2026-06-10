#!/usr/bin/env python3
"""
Example Game Server — Demonstrates the Process Adoption System.

This script is a template for long-running game servers managed by
the YuniScripts engine with the Process Adoption System enabled.

Key Features:
  - server_type=long_running in meta.info → 30s shutdown timeout
  - PID file written on start → survives engine crash
  - Process adoption on engine restart → no re-spawn needed
  - SHUTDOWN_COMPLETE marker for clean shutdown detection

Shutdown Protocol:
  1. SIGTERM from engine → set shutdown flag
  2. Break main loop
  3. Close sockets / save state
  4. Print SHUTDOWN_COMPLETE
  5. Exit cleanly
"""

import signal
import sys
import time

# Shutdown flag — set by signal handler
_shutdown_flag = False


def _handle_sigterm(signum=None, frame=None):
    """Handle termination signals gracefully."""
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    print("[server] Received shutdown signal, cleaning up...", flush=True)


def main():
    # Register platform-available signal handlers
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _handle_sigterm)
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, _handle_sigterm)

    # Register SIMULATED SIGTERM for Windows
    if sys.platform == 'win32' and hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _handle_sigterm)

    print(f"[server] Example Game Server starting (pid={__import__('os').getpid()})", flush=True)
    print(f"[server] shutdown_timeout={30}s — engine will wait 30s before force-kill", flush=True)

    try:
        tick = 0
        while not _shutdown_flag:
            # === GAME LOOP ===
            # Replace with actual game server logic
            tick += 1
            if tick % 10 == 0:
                print(f"[server] Tick #{tick} — server running smoothly", flush=True)
            time.sleep(1.0)

    except KeyboardInterrupt:
        _handle_sigterm()
    finally:
        # === CLEANUP ===
        print("[server] Saving state...", flush=True)
        time.sleep(0.5)  # Simulated save time
        print("[server] Closing connections...", flush=True)

        # Signal the engine that shutdown is complete
        print("SHUTDOWN_COMPLETE", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
