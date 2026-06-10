"""
ah_config.py — Configuration loading & defaults for the Auction House system.

Loads from:
  1. AUCTIONHOUSE/ah_config.json (override file, created on first run if missing)
  2. ../DATA/config.json (shared mc_manager config — for DeepSeek API key, RCON info)
  3. Hard-coded defaults (last resort)

Provides a single Config dataclass with all auction house settings exposed
as attributes.  Loaded once at startup and cached.
"""

import json, os, threading
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

from engine.config_loader import get_config_path, load_config

AH_DIR = Path(__file__).parent.resolve()
# Shared mc_manager config (RCON, DeepSeek API key) from centralized DATA/
SHARED_CONFIG_PATH = Path(get_config_path("minecraft_manager"))
# AH-specific override config from centralized DATA/
CONFIG_OVERRIDE_PATH = Path(get_config_path("ah"))

# ──────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────

_DEFAULTS = {
    # ── DeepSeek API ──────────────────────────────────────────────
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-v4-flash",       # "deepseek-v4-flash" or "deepseek-v4-pro"
    "deepseek_model_tier": "flash",               # "flash" (fast/cheap) or "pro" (powerful)
    "deepseek_thinking_mode": True,                # True = enable Chain-of-Thought for market AI (better quality, runs every 6h)
    "deepseek_temperature": 0.8,
    "deepseek_max_tokens": 4096,
    "deepseek_timeout_seconds": 60.0,              # Increased for thinking/pro models
    "deepseek_enable_context_cache": True,
    "ai_persona_decision": True,         # Enable automatic prompt caching (97% cheaper cache hits)

    
    # ── Persona tier (only config needed — see EXTENSIONS/SIMULATED_PEOPLE/config.json) ──
    #   "ultra_data_saver"  → max=3,  pool=10   🟢 1-3 personas
    #   "data_saver"        → max=7,  pool=25   🟢 4-7     (DEFAULT)
    #   "normal"            → max=20, pool=50   🟡 8-20
    #   "above_normal"      → max=50, pool=200  🟠 21-50
    #   "burn_my_tokens"    → max=200,pool=500  🔴 51-200
    "persona_tier": "data_saver",
# ── Simulation timers ─────────────────────────────────────────
    "simulation_enabled": True,
    "simulation_interval_minutes": 360,            # default 6 hours
    "simulation_retry_count": 3,
    "simulation_retry_delay_base": 2.0,            # exponential backoff base (s)

    # ── Event frequency defaults ──────────────────────────────────
    "event_small_interval_hours": 3,               # ~every 3 hours
    "event_medium_interval_hours": 48,              # ~every 2 days
    "event_rare_interval_hours": 336,               # ~every 2 weeks
    "event_major_interval_hours": 2160,             # ~every 3 months
    "event_major_cooldown_days": 30,
    "event_max_active_overlap": 2,                  # max simultaneous events

    # ── Auction rules ─────────────────────────────────────────────
    "max_listings_per_player": 5,
    "min_bid_increment_pct": 10,                   # e.g. 10 (%)
    "listing_fee_emeralds": 0.5,
    "sale_fee_pct": 2.0,                           # e.g. 2 (%)
    "bin_fee_pct": 1.0,
    "cancel_after_bids_fee": 1.0,
    "auction_duration_default_hours": 48,
    "auction_duration_max_hours": 168,              # 7 days
    "stale_hours_threshold": 24,                   # items with no bids for 24h = stale

    # ── Simulated inventory bounds ────────────────────────────────
    "sim_price_min": 0.01,
    "sim_price_max": 500.0,
    "sim_max_listings_per_cycle": 5,               # max new sim listings per AI cycle
    "sim_max_rare_per_cycle": 2,                    # max rare items per cycle
    "sim_ultra_rare_chance": 0.01,                  # 1% per cycle for elytra etc.

    # ── Rare item generation ──────────────────────────────────────
    "rare_item_ultra_rare_chance": 0.01,            # 1%
    "rare_item_rare_chance": 0.05,                  # 5%
    "rare_item_uncommon_chance": 0.15,              # 15%
    "rare_item_none_chance_per_cycle": 0.75,        # 75% — most cycles produce nothing

    # ── Currency ──────────────────────────────────────────────────
    "currency_item_id": "minecraft:emerald",
    "currency_name": "Emerald",
    "currency_symbol": "◆",

    # ── Startup behavior ──────────────────────────────────────────
    "reset_on_start": False,         # If True, wipe ALL data on next boot (factory reset)

    # ── Logging ───────────────────────────────────────────────────
    "log_verbose": True,
    "log_retention_days": 30,
}


@dataclass
class Config:
    """Auction House configuration — populated once at startup."""

    # DeepSeek API
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"   # "deepseek-v4-flash" or "deepseek-v4-pro"
    deepseek_model_tier: str = "flash"           # "flash" (fast/cheap) or "pro" (powerful)
    deepseek_thinking_mode: bool = True           # True = enable Chain-of-Thought for market AI
    deepseek_temperature: float = 0.8
    deepseek_max_tokens: int = 4096
    deepseek_timeout_seconds: float = 60.0       # Longer timeout for thinking/pro models
    deepseek_enable_context_cache: bool = True    # Enable automatic prompt caching

    # Simulation
    simulation_enabled: bool = True
    simulation_interval_minutes: int = 360
    simulation_retry_count: int = 3
    simulation_retry_delay_base: float = 2.0

    # Event frequency
    event_small_interval_hours: int = 3
    event_medium_interval_hours: int = 48
    event_rare_interval_hours: int = 336
    event_major_interval_hours: int = 2160
    event_major_cooldown_days: int = 30
    event_max_active_overlap: int = 2

    # Auction rules
    max_listings_per_player: int = 5
    min_bid_increment_pct: int = 10
    listing_fee_emeralds: float = 0.5
    sale_fee_pct: float = 2.0
    bin_fee_pct: float = 1.0
    cancel_after_bids_fee: float = 1.0
    auction_duration_default_hours: int = 48
    auction_duration_max_hours: int = 168
    stale_hours_threshold: int = 24

    # Simulated inventory
    sim_price_min: float = 0.01
    sim_price_max: float = 500.0
    sim_max_listings_per_cycle: int = 5
    sim_max_rare_per_cycle: int = 2
    sim_ultra_rare_chance: float = 0.01

    # Rare item generation
    rare_item_ultra_rare_chance: float = 0.01
    rare_item_rare_chance: float = 0.05
    rare_item_uncommon_chance: float = 0.15
    rare_item_none_chance_per_cycle: float = 0.75

    # Currency
    currency_item_id: str = "minecraft:emerald"
    currency_name: str = "Emerald"
    currency_symbol: str = "◆"

    # Startup behavior
    reset_on_start: bool = False

    # Logging
    log_verbose: bool = True
    log_retention_days: int = 30

    # RCON (pulled from shared config)
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = ""

    _loaded: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def _try_load_json(path: Path) -> Optional[dict]:
        try:
            if path.exists() and path.stat().st_size > 0:
                with open(path, "r") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ah_config] Warning: could not load {path}: {e}")
        return None

    def load(self) -> "Config":
        """Load config from override file + shared config, merged over defaults."""
        try:
            merged = dict(_DEFAULTS)  # start with defaults

            # 1. Shared mc_manager config (RCON details, may have deepseek key)
            shared = self._try_load_json(SHARED_CONFIG_PATH)
            if shared:
                # Map shared keys → AH config keys
                if "rcon_host" in shared:
                    merged["rcon_host"] = shared["rcon_host"]
                if "rcon_port" in shared:
                    merged["rcon_port"] = int(shared["rcon_port"])
                if "rcon_password" in shared:
                    merged["rcon_password"] = shared["rcon_password"]
                if "deepseek_api_key" in shared:
                    merged["deepseek_api_key"] = shared["deepseek_api_key"]
                # Allow user to override any AH field from shared config too
                for k in list(merged.keys()):
                    if k in shared:
                        merged[k] = shared[k]

            # 2. AH-specific override config
            override = self._try_load_json(CONFIG_OVERRIDE_PATH)
            if override:
                merged.update(override)

            # Apply to self
            for k, v in merged.items():
                if hasattr(self, k):
                    setattr(self, k, v)

            self._loaded = True

            # Write override file if it didn't exist (so user can tweak it)
            if not CONFIG_OVERRIDE_PATH.exists():
                self._write_override()

            return self

        except Exception as e:
            print(f"[ah_config] Config load failed: {e}")
            return None

    def _write_override(self):
        """Write current config as the override file for user editing."""
        try:
            d = self.to_dict()
            # Don't dump secrets to config file for security
            d.pop("rcon_password", None)
            d.pop("deepseek_api_key", None)
            with open(CONFIG_OVERRIDE_PATH, "w") as f:
                json.dump(d, f, indent=2)
        except OSError as e:
            print(f"[ah_config] Could not write override config: {e}")


# ── Global singleton ─────────────────────────────────────────────────
_lock = threading.Lock()
_instance: Optional[Config] = None


def get_config(reload: bool = False) -> Config:
    """Return the global Config singleton, loading it on first call."""
    global _instance
    if _instance is None or reload:
        with _lock:
            if _instance is None or reload:
                _instance = Config().load()
    return _instance


# ── Convenience ──────────────────────────────────────────────────────
def cfg() -> Config:
    """Short alias for get_config()."""
    return get_config()

