"""
LootPower Games Bridge — MC Manager ↔ LootPower integration.

Manages MC player account linking and game command routing between
the Minecraft Minescript client and the LootPower YuniScripts game engine.

Architecture:
  minescript (ah.py) ──Phooks──> mc_manager (main.py)
                                       │
                                  ┌────┴────┐
                                  │  Bridge  │
                                  └────┬────┘
                                       │
                                  ┌────┴────┐
                                  │ LootPower│
                                  │  Engine  │
                                  └─────────┘

Account Linking:
  Each MC player gets a unique LootPower account registration.
  - player_id = "lp_{uuid4()}" for data flow consistency
  - mc_username = Minecraft player name
  - Linked via lp_accounts.db3 in mc-manager/DATA/
"""

import json
import os
import sqlite3
import threading
import time
import uuid
import re
import sys
from pathlib import Path
from typing import Optional, Callable

# ── Paths ──────────────────────────────────────────────────────────
BRIDGE_DIR = Path(__file__).parent.parent  # FUNCTIONS/ -> minecraft_manager/
DATA_DIR = BRIDGE_DIR / "DATA"
LP_ACCOUNTS_DB = DATA_DIR / "lp_accounts.db3"

# ── Logging ────────────────────────────────────────────────────────
_log_lock = threading.Lock()
_log_buffer = []


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [GAMES-BRIDGE] {msg}"
    with _log_lock:
        _log_buffer.append(line)
        if len(_log_buffer) > 100:
            _log_buffer.pop(0)
    try:
        log_path = BRIDGE_DIR / "logs" / "lp_games_bridge.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log_path), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===================================================================
# MC Chat Formatter — converts emojis, colors, styles for MC chat
# ===================================================================
class MCChatFormatter:
    """Minecraft chat output formatter.

    Converts emoji symbols to Minecraft-compatible text and applies
    § color/style codes for proper MC chat rendering.
    """

    # Emoji → MC text mapping
    EMOJI_MAP = {
        "🎮": "&lGAME&r",
        "⚔️": "&lSWORD&r",
        "⚔": "&lSWORD&r",
        "🛡️": "&lSHIELD&r",
        "🛡": "&lSHIELD&r",
        "💰": "&6GOLD&r",
        "💎": "&bGEM&r",
        "🌟": "&eSTAR&r",
        "⭐": "&eSTAR&r",
        "🔥": "&cFIRE&r",
        "💀": "&4SKULL&r",
        "👑": "&6CROWN&r",
        "🏆": "&6TROPHY&r",
        "🎯": "&eTARGET&r",
        "🎲": "&dDICE&r",
        "💪": "&aPWR&r",
        "❤️": "&cHEART&r",
        "❤": "&cHEART&r",
        "🛠️": "&7TOOL&r",
        "🛠": "&7TOOL&r",
        "🧪": "&dPOTION&r",
        "📦": "&6CHEST&r",
        "🔮": "&dCRYSTAL&r",
        "🗡️": "&fDAGGER&r",
        "🗡": "&fDAGGER&r",
        "🏹": "&eBOW&r",
        "🧭": "&bCOMPASS&r",
        "⚡": "&eBOLT&r",
        "✨": "&eSPARKLE&r",
        "🎉": "&aCONFETTI&r",
        "🎊": "&eCONFETTI&r",
        "⛏️": "&7PICK&r",
        "⛏": "&7PICK&r",
        "🔨": "&7HAMMER&r",
        "🗺️": "&bMAP&r",
        "🗺": "&bMAP&r",
        "👤": "&fPLAYER&r",
        "🔗": "&8LINK&r",
        "✅": "&a✔&r",
        "❌": "&c✘&r",
        "⚠️": "&e⚠&r",
        "⚠": "&e⚠&r",
        "🚀": "&bROCKET&r",
        "💡": "&eBULB&r",
        "🔒": "&8LOCK&r",
        "🔓": "&aUNLOCK&r",
        "📊": "&eCHART&r",
        "📈": "&aUP&r",
        "📉": "&cDOWN&r",
        "🔄": "&7SYNC&r",
        "⚙️": "&7GEAR&r",
        "⚙": "&7GEAR&r",
        "📝": "&fNOTE&r",
        "🎵": "&dNOTE&r",
        "🔔": "&eBELL&r",
        "🗨️": "&7SPEECH&r",
        "🗨": "&7SPEECH&r",
        "💬": "&7CHAT&r",
        "🤖": "&dBOT&r",
        "👾": "&dALIEN&r",
        "🌍": "&aWORLD&r",
        "🌙": "&7MOON&r",
        "☀️": "&eSUN&r",
        "☀": "&eSUN&r",
        "💫": "&dSTAR&r",
        "🌈": "&5RAINBOW&r",
        "🎒": "&6BAG&r",
        "🔑": "&eKEY&r",
        "📜": "&6SCROLL&r",
        "🏗️": "&7BUILD&r",
        "🏗": "&7BUILD&r",
        "🎪": "&dSHOW&r",
        "🎰": "&6SLOT&r",
        "♻️": "&aRECYCLE&r",
        "♻": "&aRECYCLE&r",
        "☠️": "&4☠&r",
        "☠": "&4☠&r",
    }

    # Minecraft § color codes that work in chat
    COLORS = {
        "0": "000000", "1": "0000AA", "2": "00AA00", "3": "00AAAA",
        "4": "AA0000", "5": "AA00AA", "6": "FFAA00", "7": "AAAAAA",
        "8": "555555", "9": "5555FF", "a": "55FF55", "b": "55FFFF",
        "c": "FF5555", "d": "FF55FF", "e": "FFFF55", "f": "FFFFFF",
    }

    FORMAT_CODES = {"l": "§l", "m": "§m", "n": "§n", "o": "§o", "r": "§r"}

    @staticmethod
    def format(text: str) -> str:
        """Format text for MC chat output with emojis and colors."""
        if not text:
            return ""

        result = text

        # Replace emojis with MC-compatible text markers
        for emoji, replacement in MCChatFormatter.EMOJI_MAP.items():
            result = result.replace(emoji, replacement)

        # Ensure § color codes are preserved (Minescript echo handles §)
        # But convert any & codes to § for consistency
        result = re.sub(r'&([0-9a-fklmnor])', r'§\1', result)

        return result

    @staticmethod
    def strip_codes(text: str) -> str:
        """Strip all § color/style codes from text."""
        return re.sub(r'§[0-9a-fklmnor]', '', text)

    @staticmethod
    def header(title: str, color: str = "6") -> list:
        """Create a header/footer line with proper formatting."""
        title_stripped = MCChatFormatter.strip_codes(title)
        padding = max(2, 34 - len(title_stripped))
        bar = "═" * (padding // 2)
        return [
            f"§{color}═══ §e{title} §{color}═══",
            f"§{color}{bar*2}═══════════════════",
        ]

    @staticmethod
    def bullet(text: str, color: str = "7") -> str:
        """Format a bullet point line."""
        return f" §{color}• §f{text}"

    @staticmethod
    def key_value(key: str, value: str, key_color: str = "7", val_color: str = "f") -> str:
        """Format a key: value pair."""
        return f" §{key_color}{key}: §{val_color}{value}"

    @staticmethod
    def success(text: str) -> str:
        return f"§a[✔] §f{text}"

    @staticmethod
    def error(text: str) -> str:
        return f"§c[✘] §f{text}"

    @staticmethod
    def info(text: str) -> str:
        return f"§e[!] §f{text}"

    @staticmethod
    def game_brand(text: str) -> str:
        return f"§6[LP] §f{text}"

    @staticmethod
    def line_with_label(label: str, text: str, label_color: str = "6",
                        text_color: str = "f") -> str:
        return f" §{label_color}[{label}] §{text_color}{text}"


# ===================================================================
# LootPower Account Database — links MC usernames to LP user_ids
# ===================================================================
class LootPowerAccountDB:
    """Manages the MC-to-LootPower account mapping database.

    Schema:
      lp_accounts:
        - mc_username TEXT PRIMARY KEY   -- Minecraft player name
        - lp_user_id TEXT UNIQUE NOT NULL -- LootPower user_id (lp_<uuid>)
        - display_name TEXT               -- In-game alias (optional)
        - registered_at REAL              -- Unix timestamp
        - last_login REAL                 -- Last login timestamp
        - is_active INTEGER DEFAULT 1     -- Account active flag
        - preferences TEXT                -- JSON preferences blob

      lp_game_saves:
        - save_id TEXT PRIMARY KEY        -- Unique save ID
        - lp_user_id TEXT NOT NULL        -- FK to lp_accounts
        - game_state TEXT                 -- JSON game state snapshot
        - saved_at REAL                   -- Timestamp
        - label TEXT                      -- Save label
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(LP_ACCOUNTS_DB), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS lp_accounts (
                mc_username TEXT PRIMARY KEY,
                lp_user_id TEXT UNIQUE NOT NULL,
                display_name TEXT DEFAULT '',
                registered_at REAL NOT NULL,
                last_login REAL DEFAULT 0.0,
                is_active INTEGER DEFAULT 1,
                preferences TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS lp_game_saves (
                save_id TEXT PRIMARY KEY,
                lp_user_id TEXT NOT NULL,
                game_state TEXT DEFAULT '{}',
                saved_at REAL NOT NULL,
                label TEXT DEFAULT '',
                FOREIGN KEY (lp_user_id) REFERENCES lp_accounts(lp_user_id)
            );
        """)
        self.conn.commit()

    # --- Account management ---

    def register(self, mc_username: str, display_name: str = "") -> dict:
        """Register an MC player for LootPower.

        If the player already has an account, returns existing mapping.
        Otherwise creates a new unique LP user_id.

        Args:
            mc_username: Minecraft player name
            display_name: Optional in-game alias (defaults to mc_username)

        Returns:
            dict with status, mc_username, lp_user_id, display_name, is_new
        """
        existing = self.get_by_mc(mc_username)
        if existing:
            # Update last login
            self.conn.execute(
                "UPDATE lp_accounts SET last_login=? WHERE mc_username=?",
                (time.time(), mc_username)
            )
            self.conn.commit()
            return {
                "status": "ok",
                "mc_username": mc_username,
                "lp_user_id": existing["lp_user_id"],
                "display_name": existing.get("display_name", mc_username),
                "is_new": False,
            }

        # Generate unique LP user_id
        lp_user_id = f"lp_{uuid.uuid4().hex[:12]}"
        display = display_name or mc_username
        now = time.time()

        try:
            self.conn.execute(
                "INSERT INTO lp_accounts (mc_username, lp_user_id, display_name, registered_at, last_login) VALUES (?, ?, ?, ?, ?)",
                (mc_username, lp_user_id, display, now, now)
            )
            self.conn.commit()
            _log(f"Registered {mc_username} -> {lp_user_id} (display: {display})")
            return {
                "status": "ok",
                "mc_username": mc_username,
                "lp_user_id": lp_user_id,
                "display_name": display,
                "is_new": True,
            }
        except sqlite3.IntegrityError as e:
            return {"status": "error", "error": str(e)}

    def get_by_mc(self, mc_username: str) -> Optional[dict]:
        """Look up account by MC username."""
        row = self.conn.execute(
            "SELECT * FROM lp_accounts WHERE mc_username=?", (mc_username,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_lp_id(self, lp_user_id: str) -> Optional[dict]:
        """Look up account by LP user_id."""
        row = self.conn.execute(
            "SELECT * FROM lp_accounts WHERE lp_user_id=?", (lp_user_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_display_name(self, mc_username: str, new_name: str) -> bool:
        """Update player's display name / alias."""
        try:
            self.conn.execute(
                "UPDATE lp_accounts SET display_name=? WHERE mc_username=?",
                (new_name, mc_username)
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def deactivate(self, mc_username: str) -> bool:
        """Deactivate an account."""
        try:
            self.conn.execute(
                "UPDATE lp_accounts SET is_active=0 WHERE mc_username=?",
                (mc_username,)
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def list_all(self) -> list:
        """List all registered accounts."""
        rows = self.conn.execute(
            "SELECT * FROM lp_accounts ORDER BY registered_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_active(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as c FROM lp_accounts WHERE is_active=1"
        ).fetchone()
        return row["c"] if row else 0

    # --- Game save management ---

    def save_game_state(self, lp_user_id: str, state: dict, label: str = "") -> str:
        """Save a game state snapshot."""
        save_id = f"sv_{uuid.uuid4().hex[:8]}"
        try:
            self.conn.execute(
                "INSERT INTO lp_game_saves (save_id, lp_user_id, game_state, saved_at, label) VALUES (?, ?, ?, ?, ?)",
                (save_id, lp_user_id, json.dumps(state), time.time(), label)
            )
            self.conn.commit()
            return save_id
        except Exception:
            return ""

    def get_saves(self, lp_user_id: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM lp_game_saves WHERE lp_user_id=? ORDER BY saved_at DESC",
            (lp_user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ===================================================================
# LootPower Game Bridge — main entry point for game commands
# ===================================================================
class LootPowerGameBridge:
    """Bridge between MC chat commands and the LootPower game engine.

    This bridge handles:
    1. Account registration / linking
    2. Game command routing (adventure, mine, craft, auction, etc.)
    3. Response formatting for MC chat

    The actual LootPower game engine is imported and called directly
    since both are YuniScripts modules in the same project.
    """

    def __init__(self):
        self.accounts = LootPowerAccountDB()
        self.formatter = MCChatFormatter()
        self._lp_engine = None  # Lazily loaded

    def _get_lp_engine(self):
        """Lazy-import the LootPower game engine modules."""
        if self._lp_engine is not None:
            return self._lp_engine

        try:
            # Add the lootpower dir to sys.path
            lp_dir = str(BRIDGE_DIR.parent / "lootpower")
            if lp_dir not in sys.path:
                sys.path.insert(0, lp_dir)

            import lp_player
            import lp_chance
            import lp_mining
            import lp_crafting
            import lp_adventure
            import lp_auction
            import lp_database
            import lp_leaderboard

            self._lp_engine = {
                "player": lp_player.PlayerService(),
                "chance": lp_chance,
                "mining": lp_mining,
                "crafting": lp_crafting,
                "adventure": lp_adventure,
                "auction": lp_auction,
                "database": lp_database,
                "leaderboard": lp_leaderboard,
                "loaded": True,
            }
            _log("LootPower engine modules loaded")
        except ImportError as e:
            _log(f"LootPower engine not available: {e}")
            self._lp_engine = {"loaded": False, "error": str(e)}

        return self._lp_engine

    # --- Public API used by main.py ---

    def handle_lootpower_command(self, player_name: str, args: list) -> list:
        """Handle a \ah games lootpower <subcommand> [args...] request.

        Args:
            player_name: Minecraft player name
            args: List of subcommand arguments

        Returns:
            list of formatted MC chat lines
        """
        if not args:
            return self._show_lootpower_help(player_name)

        subcmd = args[0].lower()

        if subcmd == "help":
            return self._show_lootpower_help(player_name)
        elif subcmd == "register":
            return self._cmd_register(player_name, args[1:])
        elif subcmd == "profile":
            return self._cmd_profile(player_name, args[1:])
        elif subcmd == "adventure":
            return self._cmd_adventure(player_name, args[1:])
        elif subcmd == "mine":
            return self._cmd_mine(player_name, args[1:])
        elif subcmd == "craft":
            return self._cmd_craft(player_name, args[1:])
        elif subcmd == "inventory":
            return self._cmd_inventory(player_name, args[1:])
        elif subcmd == "leaderboard":
            return self._cmd_leaderboard(player_name, args[1:])
        elif subcmd == "stats":
            return self._cmd_stats(player_name, args[1:])
        elif subcmd == "alias":
            return self._cmd_alias(player_name, args[1:])
        elif subcmd == "status":
            return self._cmd_status(player_name)
        else:
            return [self.formatter.error(f"Unknown lootpower command: {subcmd}"),
                    self.formatter.info("Use §e\\ah games lootpower help§f for available commands")]

    def get_game_announcement(self) -> list:
        """Generate game announcement for MC chat broadcast."""
        engine = self._get_lp_engine()
        player_count = self.accounts.count_active()
        lp_status = "§aOnline" if engine.get("loaded") else "§cOffline"

        return [
            "§6═══════════════════════════════════════",
            "§6  🎮  §eLootPower Game §6🎮",
            "§6═══════════════════════════════════════",
            f" §7Status: {lp_status}",
            f" §7Registered players: §f{player_count}",
            " §7Try §e\\ah games lootpower§7 to start playing!",
            " §7Commands: register, adventure, mine, craft, inventory, leaderboard",
            "§6═══════════════════════════════════════",
        ]

    # --- Command handlers (private) ---

    def _ensure_registered(self, player_name: str) -> Optional[dict]:
        """Ensure player is registered. Returns account dict or None."""
        account = self.accounts.get_by_mc(player_name)
        if not account:
            return None
        # Ensure LP user exists in actual game engine (create if not)
        engine = self._get_lp_engine()
        if engine.get("loaded"):
            psvc = engine["player"]
            display_name = account.get("display_name", player_name)
            if not psvc.user_exists(account["lp_user_id"]):
                # Check if display_name already taken in LP engine
                if psvc.user_exists(display_name):
                    # Username taken — update our mapping to existing LP ID
                    existing = engine["database"].get_db().fetchone(
                        "SELECT user_id FROM users WHERE user_name=?", (display_name,)
                    )
                    if existing:
                        old_id = account["lp_user_id"]
                        account["lp_user_id"] = existing["user_id"]
                        # Update our account DB
                        self.accounts.conn.execute(
                            "UPDATE lp_accounts SET lp_user_id=? WHERE mc_username=?", 
                            (account["lp_user_id"], player_name)
                        )
                        self.accounts.conn.commit()
                else:
                    # Create new LP user
                    psvc.create_user(
                        account["lp_user_id"],
                        display_name,
                        f"mc_{account['lp_user_id']}"  # MC-linked password
                    )
        return account

    def _show_lootpower_help(self, player_name: str) -> list:
        lines = [
            "§6═══ §eLootPower — Commands §6═══",
            self.formatter.bullet("§e\\ah games lootpower register §7- Set up your account"),
            self.formatter.bullet("§e\\ah games lootpower profile §7- View your profile"),
            self.formatter.bullet("§e\\ah games lootpower adventure §7- Go on an adventure!"),
            self.formatter.bullet("§e\\ah games lootpower mine §7- Try your luck mining"),
            self.formatter.bullet("§e\\ah games lootpower craft [id1] [id2] §7- Craft items"),
            self.formatter.bullet("§e\\ah games lootpower inventory §7- Check your loot"),
            self.formatter.bullet("§e\\ah games lootpower leaderboard §7- Top players"),
            self.formatter.bullet("§e\\ah games lootpower stats §7- Your statistics"),
            self.formatter.bullet("§e\\ah games lootpower alias [name] §7- Set display name"),
            self.formatter.bullet("§e\\ah games lootpower status §7- Game server status"),
            "§6═════════════════════════════════",
            "§7First time? Use §e\\ah games lootpower register§7 to get started!",
        ]
        return lines

    def _cmd_register(self, player_name: str, args: list) -> list:
        """Register this MC player for LootPower."""
        # Check if already registered
        account = self.accounts.get_by_mc(player_name)
        if account:
            engine = self._get_lp_engine()
            lp_ok = engine.get("loaded", False)
            return [
                self.formatter.success(f"You're already registered, {player_name}!"),
                self.formatter.key_value("LP User ID", account["lp_user_id"], "7", "b"),
                self.formatter.key_value("Display Name", account.get("display_name", player_name), "7", "f"),
                self.formatter.key_value("Game Status", "§aReady" if lp_ok else "§cUnavailable", "7", "f"),
                self.formatter.info("Use §e\\ah games lootpower adventure§f to start playing!"),
            ]

        # Ask for alias preference
        display_name = " ".join(args) if args else player_name

        result = self.accounts.register(player_name, display_name)
        if result["status"] != "ok":
            return [self.formatter.error(f"Registration failed: {result.get('error', 'Unknown error')}")]

        # Create the LP user in the game engine
        engine = self._get_lp_engine()
        if engine.get("loaded"):
            psvc = engine["player"]
            psvc.create_user(
                result["lp_user_id"],
                display_name,
                f"mc_{result['lp_user_id']}"
            )

        return [
            "§6═══ §eLootPower — Registration Complete! §6═══",
            self.formatter.success(f"Welcome, {display_name}!"),
            self.formatter.key_value("MC Username", player_name, "7", "f"),
            self.formatter.key_value("LP User ID", result["lp_user_id"], "7", "b"),
            self.formatter.key_value("Display Name", display_name, "7", "f"),
            "§7─" * 18,
            self.formatter.info("You're all set! Try these commands:"),
            self.formatter.bullet("§e\\ah games lootpower adventure §7- Start an adventure!"),
            self.formatter.bullet("§e\\ah games lootpower mine §7- Go mining!"),
            self.formatter.bullet("§e\\ah games lootpower inventory §7- Check your loot"),
            "§6═══════════════════════════════════════",
        ]

    def _cmd_profile(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("You need to register first! Use §e\\ah games lootpower register"),
                    self.formatter.info("Or use §e\\ah games lootpower register [alias]§f with a custom name")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("LootPower game engine is offline.")]

        psvc = engine["player"]
        profile = psvc.get_profile(account["lp_user_id"])
        if not profile:
            return [self.formatter.error("Profile not found. Please re-register.")]

        turns = profile.get("turns", 0)
        lootpower = profile.get("lootpower", 0.0)
        items = profile.get("total_items", 0)
        cooldown = psvc.get_turn_cooldown(account["lp_user_id"])

        lines = [
            "§6═══ §eLootPower — Profile §6═══",
            self.formatter.key_value("Player", account.get("display_name", player_name), "7", "f"),
            self.formatter.key_value("LP ID", account["lp_user_id"], "7", "b"),
            self.formatter.key_value("Turns", str(turns), "7", "e"),
        ]

        if cooldown > 0:
            lines.append(self.formatter.key_value("Cooldown", f"{cooldown:.0f}s remaining", "7", "c"))
        else:
            lines.append(self.formatter.key_value("Status", "Ready for adventure!", "7", "a"))

        lines += [
            self.formatter.key_value("LootPower", f"{lootpower:.1f}", "7", "d"),
            self.formatter.key_value("Items Collected", str(items), "7", "f"),
            "§6═════════════════════════════════",
        ]
        return lines

    def _cmd_adventure(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        psvc = engine["player"]
        remaining = psvc.validate_turn(account["lp_user_id"])
        if remaining <= 0:
            cooldown = psvc.get_turn_cooldown(account["lp_user_id"])
            if cooldown > 0:
                return [self.formatter.error(f"On cooldown! Wait {cooldown:.0f}s.")]
            return [self.formatter.error("No turns remaining! Wait for replenishment.")]

        # Execute adventure
        try:
            adv_mod = engine["adventure"]
            result = adv_mod.perform_adventure(account["lp_user_id"], account.get("display_name", player_name))
            if isinstance(result, dict):
                lines = [
                    "§6═══ §eAdventure Result! §6═══",
                    self.formatter.game_brand(result.get("story", "You ventured forth!")),
                ]
                loot = result.get("loot", {})
                if loot:
                    name = loot.get("name", "something")
                    rarity = loot.get("rarity", "common")
                    lines.append(self.formatter.success(f"You found: {name} ({rarity})!"))
                else:
                    lines.append(self.formatter.info("You found nothing this time..."))
                lines.append(self.formatter.key_value("Turns Left", str(remaining - 1), "7", "e"))
                lines.append("§6═════════════════════════════════")
                return lines
            return [self.formatter.game_brand(str(result)[:200])]
        except Exception as e:
            _log(f"Adventure error for {player_name}: {e}")
            return [self.formatter.error(f"Adventure failed: {str(e)[:100]}")]

    def _cmd_mine(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        psvc = engine["player"]
        remaining = psvc.validate_turn(account["lp_user_id"])
        if remaining <= 0:
            cooldown = psvc.get_turn_cooldown(account["lp_user_id"])
            if cooldown > 0:
                return [self.formatter.error(f"On cooldown! Wait {cooldown:.0f}s.")]
            return [self.formatter.error("No turns remaining.")]

        try:
            mine_mod = engine["mining"]
            result = mine_mod.perform_mining(account["lp_user_id"])
            if isinstance(result, dict):
                lines = [
                    "§6═══ §eMining Result! §6═══",
                ]
                ore = result.get("ore", "bag_of_dirt")
                amount = result.get("amount", 1)
                ore_display = ore.replace("_", " ").title()
                lines.append(self.formatter.success(f"You mined {amount}x {ore_display}!"))
                lines.append(self.formatter.key_value("Turns Left", str(remaining - 1), "7", "e"))
                lines.append("§6═════════════════════════════════")
                return lines
            return [self.formatter.game_brand(str(result)[:200])]
        except Exception as e:
            _log(f"Mining error for {player_name}: {e}")
            return [self.formatter.error(f"Mining failed: {str(e)[:100]}")]

    def _cmd_craft(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        if len(args) < 2:
            return [self.formatter.error("Usage: §e\\ah games lootpower craft <item1> <item2>")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        try:
            item1_id = int(args[0])
            item2_id = int(args[1])
        except ValueError:
            return [self.formatter.error("Item IDs must be numbers. Check §e\\ah games lootpower inventory")]

        try:
            craft_mod = engine["crafting"]
            result = craft_mod.perform_craft(account["lp_user_id"], item1_id, item2_id)
            if isinstance(result, dict):
                lines = [
                    "§6═══ §eCrafting Result! §6═══",
                ]
                if result.get("success"):
                    created = result.get("created", "something")
                    lines.append(self.formatter.success(f"You crafted: {created}!"))
                else:
                    lines.append(self.formatter.info(f"Crafting failed: {result.get('reason', 'Mismatch')}"))
                lines.append("§6═════════════════════════════════")
                return lines
            return [self.formatter.game_brand(str(result)[:200])]
        except Exception as e:
            _log(f"Craft error for {player_name}: {e}")
            return [self.formatter.error(f"Crafting failed: {str(e)[:100]}")]

    def _cmd_inventory(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        psvc = engine["player"]
        inventory = psvc.get_inventory(account["lp_user_id"])

        if not inventory:
            return [
                self.formatter.info("Your inventory is empty!"),
                self.formatter.info("Go on an adventure: §e\\ah games lootpower adventure"),
            ]

        lines = [
            f"§6═══ §e{account.get('display_name', player_name)}'s Loot §6═══",
        ]
        for item in inventory[:20]:
            item_name = item.get("loot", "?")
            amount = item.get("loot_amount", 0)
            # Show rarity breakdown if available
            rarity_info = ""
            rarity_colors = {"common": "7", "uncommon": "a", "rare": "b",
                           "great": "d", "amazing": "6", "legendary": "e",
                           "epic": "5", "godly": "c", "mythic": "4"}
            for r, color in rarity_colors.items():
                count = item.get(r, 0)
                if count > 0:
                    rarity_info += f" §{color}{r[:3]}={count}"

            lines.append(f" §7• §f{item_name} §7x{amount}{rarity_info}")

        if len(inventory) > 20:
            lines.append(f" §7... and {len(inventory)-20} more items.")

        lines.append("§6═════════════════════════════════")
        return lines

    def _cmd_leaderboard(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        try:
            lb_mod = engine["leaderboard"]
            top = lb_mod.get_top_players(limit=10)

            lines = [
                "§6═══ §eLootPower Leaderboard §6═══",
            ]
            if not top:
                lines.append(self.formatter.info("No players yet. Be the first!"))
            else:
                for i, entry in enumerate(top, 1):
                    name = entry.get("user_name", entry.get("user_id", "?"))
                    power = entry.get("lootpower", 0)
                    medal = "§6★" if i == 1 else "§7☆" if i <= 3 else " "
                    lines.append(f" {medal} §f#{i:<2} §e{name:<16} §7- §d{power:.1f} LP")

            lines.append("§6═════════════════════════════════")
            return lines
        except Exception as e:
            _log(f"Leaderboard error: {e}")
            return [self.formatter.error(f"Leaderboard error: {str(e)[:100]}")]

    def _cmd_stats(self, player_name: str, args: list) -> list:
        account = self._ensure_registered(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        engine = self._get_lp_engine()
        if not engine.get("loaded"):
            return [self.formatter.error("Game engine offline.")]

        psvc = engine["player"]
        profile = psvc.get_profile(account["lp_user_id"])
        if not profile:
            return [self.formatter.error("Profile not found.")]

        turns = profile.get("turns", 0)
        lootpower = profile.get("lootpower", 0.0)
        items = profile.get("total_items", 0)

        return [
            "§6═══ §eLootPower — Stats §6═══",
            self.formatter.key_value("Player", account.get("display_name", player_name), "7", "f"),
            self.formatter.key_value("Turns Remaining", str(turns), "7", "e"),
            self.formatter.key_value("LootPower Score", f"{lootpower:.1f}", "7", "d"),
            self.formatter.key_value("Total Items", str(items), "7", "f"),
            "§6═════════════════════════════════",
        ]

    def _cmd_alias(self, player_name: str, args: list) -> list:
        account = self.accounts.get_by_mc(player_name)
        if not account:
            return [self.formatter.error("Register first! §e\\ah games lootpower register")]

        if not args:
            current = account.get("display_name", player_name)
            return [self.formatter.info(f"Your display name is: §f{current}"),
                    self.formatter.info("Change it: §e\\ah games lootpower alias <new_name>")]

        new_name = " ".join(args)
        if len(new_name) > 32:
            return [self.formatter.error("Display name too long (max 32 chars).")]

        if self.accounts.update_display_name(player_name, new_name):
            return [self.formatter.success(f"Display name changed to: §f{new_name}")]
        return [self.formatter.error("Failed to update display name.")]

    def _cmd_status(self, player_name: str) -> list:
        engine = self._get_lp_engine()
        online = engine.get("loaded", False)
        player_count = self.accounts.count_active()

        _db_ok = str(LP_ACCOUNTS_DB.exists())
        db_status = "§a✓" if LP_ACCOUNTS_DB.exists() else "§c✗"
        lines = [
            "§6═══ §eLootPower — Status §6═══",
            f" §7Game Server: {'§aOnline' if online else '§cOffline'}",
            f" §7Registered Players: §f{player_count}",
            f" §7Account DB: {db_status}",
        ]

        if online:
            try:
                psvc = engine["player"]
                db = engine["database"].get_db()
                total_users = db.fetchone("SELECT COUNT(*) as c FROM users")
                lines.append(f" §7Total Game Users: §f{total_users['c'] if total_users else 0}")
            except Exception:
                pass

        lines.append("§6═════════════════════════════════")
        return lines


# ===================================================================
# Singleton accessors
# ===================================================================
_bridge_instance = None
_bridge_lock = threading.Lock()


def get_bridge() -> LootPowerGameBridge:
    """Get the singleton LootPower game bridge."""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = LootPowerGameBridge()
        return _bridge_instance


def get_formatter() -> MCChatFormatter:
    """Get the MC chat formatter."""
    return MCChatFormatter()


def get_account_db() -> LootPowerAccountDB:
    """Get the account database singleton."""
    return LootPowerAccountDB()
