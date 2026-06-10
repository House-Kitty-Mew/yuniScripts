#!/usr/bin/env python3
"""
Systems Admin Notification System — YuniScripts Self-Healing Escalation Handler.
WO #207 Deliverable.

When the self-healing AI hits an extreme case it cannot fix, this script:
  1. Sends a desktop notification (notify-send)
  2. Plays a repeating alert sound until an admin acknowledges the issue
  3. Logs the notification for audit

Usage:
  # Send a notification (blocks with repeating sound until ack'd)
  python3 admin_notifier.py --send \\
      --title "WO #203 Escalated" \\
      --message "Sub-agent tool execution gap requires manual fix" \\
      --priority 1 \\
      --work-order 203

  # Acknowledge a notification
  python3 admin_notifier.py --ack <uuid>

  # List all active notifications
  python3 admin_notifier.py --list

  # Acknowledge all
  python3 admin_notifier.py --ack-all

Integration:
  In healing_agent.py, call this via subprocess.Popen(non-blocking):
    subprocess.Popen([
        sys.executable, ADMIN_NOTIFIER_PATH,
        '--send', '--title', title, '--message', msg,
        '--priority', str(priority), '--work-order', str(wo_id)
    ])
"""

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ─── Paths ───────────────────────────────────────────────────────

NOTIFY_DIR = Path("/tmp/admin_notifications")
LOG_DIR = Path.home() / "Documents" / "dev-yuniScripts" / "DATA"
LOG_FILE = LOG_DIR / "admin_notifications.log"
SOUND_DIR = Path(tempfile.gettempdir())

# ─── Logging ─────────────────────────────────────────────────────

logger = logging.getLogger("admin_notifier")
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s - admin_notifier - %(levelname)s - %(message)s"
))
logger.addHandler(_console_handler)
logger.setLevel(logging.INFO)


# ─── Data Model ──────────────────────────────────────────────────

class Notification:
    """A single admin notification."""

    def __init__(self, uid: str, title: str, message: str,
                 priority: int = 3, work_order: Optional[int] = None,
                 component: str = "unknown"):
        self.uid = uid
        self.title = title
        self.message = message
        self.priority = priority
        self.work_order = work_order
        self.component = component
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.acked = False
        self.acked_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "message": self.message,
            "priority": self.priority,
            "work_order": self.work_order,
            "component": self.component,
            "created_at": self.created_at,
            "acked": self.acked,
            "acked_at": self.acked_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Notification":
        n = cls(
            uid=data["uid"], title=data["title"], message=data["message"],
            priority=data.get("priority", 3),
            work_order=data.get("work_order"),
            component=data.get("component", "unknown"),
        )
        n.created_at = data.get("created_at", n.created_at)
        n.acked = data.get("acked", False)
        n.acked_at = data.get("acked_at")
        return n

    def file_path(self) -> Path:
        return NOTIFY_DIR / f"{self.uid}.json"


# ─── Audio ───────────────────────────────────────────────────────

def _generate_alert_wav(path: Path, frequency: float = 880.0,
                        duration: float = 0.3, volume: float = 0.5):
    """Generate a WAV alert sound file using pure Python (no deps)."""
    import struct
    import math

    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    data = []
    for i in range(num_samples):
        t = i / sample_rate
        # Square wave at frequency for piercing alert sound
        value = 1.0 if (t * frequency) % 1.0 < 0.5 else -1.0
        # Apply envelope (fade in/out to avoid clicks)
        envelope = 1.0
        fade_len = int(num_samples * 0.05)
        if i < fade_len:
            envelope = i / fade_len
        elif i > num_samples - fade_len:
            envelope = (num_samples - i) / fade_len
        sample = int(volume * envelope * value * 32767)
        data.append(struct.pack('<h', max(-32768, min(32767, sample))))

    with open(path, 'wb') as f:
        import wave
        with wave.open(f, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(b''.join(data))

    return path


def _play_sound(wav_path: Path):
    """Play a sound file using available audio tools."""
    # Try paplay (PulseAudio), then aplay, then ogg123, then terminal bell
    for player, args in [
        ("paplay", [str(wav_path)]),
        ("aplay", ["-q", str(wav_path)]),
        ("ogg123", ["-q", str(wav_path)]),
    ]:
        if shutil.which(player):
            try:
                subprocess.run(
                    [player] + args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return True
            except (subprocess.TimeoutExpired, OSError):
                continue

    # Fallback: terminal bell
    sys.stderr.write("\a")
    sys.stderr.flush()
    return False


# ─── Desktop Notification ────────────────────────────────────────

_LAST_NOTIFY_TIME = 0.0


def _send_desktop_notification(title: str, message: str, priority: int = 3,
                                uid: str = ""):
    """Send a desktop notification and log it."""
    global _LAST_NOTIFY_TIME

    urgency = "critical" if priority <= 2 else "normal"

    # Try notify-send
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send",
                 "--urgency", urgency,
                 "--expire-time", "0",  # Never auto-expire
                 "--app-name", "DeepSky Healer",
                 title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Also try zenity for systems without notify-send
    if shutil.which("zenity"):
        try:
            subprocess.Popen(
                ["zenity", "--warning",
                 "--title", title,
                 "--text", f"{message}\n\nTo acknowledge: admin_notifier.py --ack {uid}",
                 "--width", "500"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    _LAST_NOTIFY_TIME = time.time()
    logger.info(f"Notification sent: [{title}] {message[:80]}")


# ─── Persistence ─────────────────────────────────────────────────

def _ensure_dirs():
    """Create notification directories if needed."""
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _save_notification(notification: Notification):
    """Save a notification as a JSON file."""
    _ensure_dirs()
    with open(notification.file_path(), 'w') as f:
        json.dump(notification.to_dict(), f, indent=2)


def _load_notification(uid: str) -> Optional[Notification]:
    """Load a notification by UID."""
    path = NOTIFY_DIR / f"{uid}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return Notification.from_dict(json.load(f))
    except (json.JSONDecodeError, KeyError):
        return None


def _list_notifications() -> List[Notification]:
    """List all active (unacked) notifications."""
    if not NOTIFY_DIR.exists():
        return []
    notifications = []
    for fpath in sorted(NOTIFY_DIR.iterdir()):
        if fpath.suffix == '.json':
            try:
                with open(fpath) as f:
                    n = Notification.from_dict(json.load(f))
                    if not n.acked:
                        notifications.append(n)
            except (json.JSONDecodeError, KeyError):
                continue
    return notifications


def _remove_notification(uid: str) -> bool:
    """Remove a notification file (acknowledge it)."""
    path = NOTIFY_DIR / f"{uid}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _log_notification(notification: Notification):
    """Log a notification event to the log file."""
    _ensure_dirs()
    mode = 'a' if LOG_FILE.exists() else 'w'
    with open(LOG_FILE, mode) as f:
        if mode == 'w':
            f.write("# Admin Notification Log\n")
            f.write("# Format: TIMESTAMP | LEVEL | UID | TITLE | WO | MESSAGE\n")
        f.write(
            f"{notification.created_at} | "
            f"{'ACKED' if notification.acked else 'SENT'} | "
            f"{notification.uid} | "
            f"{notification.title} | "
            f"WO#{notification.work_order or '-'} | "
            f"{notification.message[:120]}\n"
        )
    logger.info(f"Logged notification {notification.uid}")


# ─── Main Operations ─────────────────────────────────────────────

def cmd_send(args):
    """Send a notification and loop sound until ack'd."""
    notification = Notification(
        uid=str(uuid.uuid4()),
        title=args.title,
        message=args.message,
        priority=args.priority,
        work_order=args.work_order,
        component=args.component or "healing_agent",
    )

    # Save and log
    _save_notification(notification)
    _log_notification(notification)

    # Desktop notification
    _send_desktop_notification(
        notification.title, notification.message,
        notification.priority, notification.uid
    )

    # Generate alert sound
    sound_wav = SOUND_DIR / f"alert_{notification.uid}.wav"
    _generate_alert_wav(sound_wav)

    # Sound loop — keeps playing until ack'd
    alert_intervals = [5, 5, 5, 10, 10, 15, 15, 30, 30, 30]
    loop_count = 0
    acked = False

    def _signal_handler(sig, frame):
        nonlocal acked
        acked = True
        logger.info("SIGINT received, stopping sound loop")
        if sound_wav.exists():
            sound_wav.unlink()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"\n{'='*60}")
    print(f"⚠️  ADMIN NOTIFICATION — ACTION REQUIRED")
    print(f"{'='*60}")
    print(f"  UID:       {notification.uid}")
    print(f"  Title:     {notification.title}")
    print(f"  Message:   {notification.message}")
    print(f"  Priority:  P{notification.priority}")
    print(f"  Work Order: #{notification.work_order}" if notification.work_order else "")
    print(f"  Time:      {notification.created_at}")
    print(f"{'='*60}")
    print(f"  🔴 Sound is playing — press Enter or Ctrl+C to acknowledge")
    print(f"  Or run: admin_notifier.py --ack {notification.uid}")
    print(f"{'='*60}\n")

    while not acked and not notification.acked:
        # Play sound
        _play_sound(sound_wav)

        # Wait with polling for ack
        interval = alert_intervals[min(loop_count, len(alert_intervals) - 1)]
        for _ in range(interval * 2):  # Check every 0.5s
            if acked:
                break
            # Check if file was removed (ack'd externally)
            if not notification.file_path().exists():
                notification.acked = True
                break
            time.sleep(0.5)

        loop_count += 1

        # Escalate urgency message every 30s
        if loop_count % 6 == 0:
            print(f"  ⏰ Still waiting for admin acknowledgment... "
                  f"({int(loop_count * interval / 60)}m elapsed)")

    # Cleanup
    notification.acked = True
    notification.acked_at = datetime.now(timezone.utc).isoformat()
    _remove_notification(notification.uid)
    _log_notification(notification)

    if sound_wav.exists():
        sound_wav.unlink()

    # Clear the desktop notification with a follow-up
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send",
                 "--urgency", "normal",
                 "--expire-time", "3000",
                 "--app-name", "DeepSky Healer",
                 "✅ Issue Acknowledged",
                 f"{notification.title} acknowledged by admin"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except OSError:
            pass

    print(f"\n✅ Acknowledged: {notification.title}")
    return 0


def cmd_ack(args):
    """Acknowledge a specific notification."""
    uid = args.ack
    notification = _load_notification(uid)
    if notification:
        notification.acked = True
        notification.acked_at = datetime.now(timezone.utc).isoformat()
        _remove_notification(uid)
        _log_notification(notification)
        print(f"✅ Acknowledged notification: {uid} ({notification.title})")
        logger.info(f"Admin acknowledged notification {uid}")
        return 0
    else:
        print(f"❌ Notification not found: {uid}")
        return 1


def cmd_list(args):
    """List all active notifications."""
    notifications = _list_notifications()
    if not notifications:
        print("✅ No active notifications.")
        return 0

    print(f"\n{'='*60}")
    print(f"⚠️  ACTIVE ADMIN NOTIFICATIONS ({len(notifications)})")
    print(f"{'='*60}")
    for n in notifications:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(n.created_at.replace('Z', '+00:00')))
        minutes = int(age.total_seconds() / 60)
        print(f"\n  [{n.uid[:8]}] P{n.priority} | "
              f"{minutes}m ago | {'WO#' + str(n.work_order) if n.work_order else 'N/A'}")
        print(f"       {n.title}")
        print(f"       To ack: admin_notifier.py --ack {n.uid}")
    print(f"\n{'='*60}\n")
    return 0


def cmd_ack_all(args):
    """Acknowledge all active notifications."""
    notifications = _list_notifications()
    if not notifications:
        print("✅ No active notifications to acknowledge.")
        return 0

    count = 0
    for n in notifications:
        n.acked = True
        n.acked_at = datetime.now(timezone.utc).isoformat()
        _remove_notification(n.uid)
        _log_notification(n)
        count += 1
        print(f"  ✅ Acknowledged: {n.uid[:8]} - {n.title}")

    print(f"\n✅ Ack'd {count} notification(s).")
    return 0


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
    
    description="DeepSky Admin Notification System — alerts admin for unfixable issues"
    
    )

    # Mutually exclusive action group
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--send", action="store_true",
                       help="Send a notification (blocks with sound until ack'd)")
    action.add_argument("--ack", type=str, metavar="UUID",
                       help="Acknowledge a notification by UUID")
    action.add_argument("--list", action="store_true",
                       help="List all active notifications")
    action.add_argument("--ack-all", action="store_true",
                       help="Acknowledge all active notifications")

    # Send-specific args
    parser.add_argument("--title", type=str, default="DeepSky Alert",
                       help="Notification title")
    parser.add_argument("--message", type=str, default="",
                       help="Notification message")
    parser.add_argument("--priority", type=int, default=3, choices=range(1, 6),
                       help="Priority level (1-5, 1=critical)")
    parser.add_argument("--work-order", type=int, default=None,
                       help="Associated work order number")
    parser.add_argument("--component", type=str, default=None,
                       help="Component that triggered the notification")

    args = parser.parse_args()

    # Route to command handler
    if args.send:
        return cmd_send(args)
    elif args.ack:
        return cmd_ack(args)
    elif args.list:
        return cmd_list(args)
    elif args.ack_all:
        return cmd_ack_all(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

