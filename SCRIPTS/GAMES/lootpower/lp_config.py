"""
LootPower Configuration — all configurable values in one place.
"""
from pathlib import Path

# --- Paths ---
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "DATA"
DB_PATH = DATA_DIR / "lootpower.db3"

# --- State flags ---
DRY_RUN = False          # When True, no writes to DB (preview mode)
WATCHER_ENABLED = True   # Allow observer connections

# --- Network ---
UDP_PORT = 9483          # Main game server
PHOOKS_PORT = 7950       # Phooks event bus port
LAN_BROADCAST = True     # Enable LAN discovery beacon

# --- Game constants ---
MAX_TURNS = 20
TURN_COOLDOWN = 5        # Seconds between adventure turns
TURN_REPLENISH_INTERVAL = 60  # Seconds between auto-refill ticks

# --- Rarity levels (matching original exactly) ---
RARITY_NAMES = [
    "common", "uncommon", "rare", "great", "amazing",
    "legendary", "epic", "godly", "mythic"
]
RARITY_ROLL_VALUES = [2, 10, 25, 50, 100, 250, 500, 1000, 1000000]

# --- Mining ---
MINE_ORE_WEIGHTS = {
    "bag_of_dirt": 0.60,
    "power_coin": 0.20,
    "loot_ore": 0.20,
}

# --- Encryption (legacy compat) ---
ENCRYPTION_SALT = b"lootwhore"
ENCRYPTION_N = 1024
ENCRYPTION_R = 1
ENCRYPTION_P = 1
ENCRYPTION_DKLEN = 32

# --- Phooks event names ---
PHOOK_EVENTS = {
    "LOOT_DROPPED": "lootpower:loot_dropped",
    "ADVENTURE_START": "lootpower:adventure_start",
    "ADVENTURE_COMPLETE": "lootpower:adventure_complete",
    "CRAFT_ATTEMPT": "lootpower:craft_attempt",
    "CRAFT_COMPLETE": "lootpower:craft_complete",
    "MINE_HIT": "lootpower:mine_hit",
    "TURN_REPLENISH": "lootpower:turn_replenish",
    "LEVEL_UP": "lootpower:level_up",
    "MARKET_ASK": "lootpower:market_ask",
    "WATCHER_EVENT": "lootpower:watcher_event",
}

# --- Runtime codes ---
RUNTIME_NORMAL = 0
RUNTIME_SHUTDOWN = 1
RUNTIME_RESTART = 2

# --- Legacy op-codes (for reference) ---
OP_LOGIN = "login"
OP_ADVENTURE = "1"
OP_LIST_LOOT = "2"
OP_SELL_LOOT = "3"
OP_LOOTPOWER_CHECK = "4"
OP_LOGOUT = "6"
OP_CRAFT = "8"
OP_MINE = "10"