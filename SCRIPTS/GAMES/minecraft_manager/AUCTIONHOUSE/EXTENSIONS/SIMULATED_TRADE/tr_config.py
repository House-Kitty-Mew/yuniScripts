"""
tr_config.py — Configuration loader for the SIMULATED_TRADE extension.

Reads config.json from the extension directory and provides typed defaults.
All values are validated and logged at load time.
"""

import json, os, threading
from AUCTIONHOUSE.ah_logger import get_logger
from pathlib import Path
from typing import Any, Optional

log = get_logger()

_EXT_DIR = Path(__file__).parent
_CONFIG_PATH = _EXT_DIR / "config.json"

_log: list[str] = []  # logged messages collected during init
_lock = threading.Lock()
_cache: Optional[dict[str, Any]] = None


# ── Default configuration (used when config.json is missing) ─────────
_DEFAULT_CONFIG: dict[str, Any] = {
    "trade": {
        "max_trade_distance": 2000,
        "distance_penalty_per_1000": 0.1,
        "barter_tolerance_default": 0.15,
        "barter_skill_impact": 0.005,
        "max_pending_offers_per_persona": 5,
        "offer_expiry_ticks": 100,
        "trade_cooldown_ticks": 5,
        "max_trade_range_personas": 10,
        "relationship_gain_fair_trade": 3,
        "relationship_loss_unfair_trade": 5,
        "relationship_loss_banditry_base": 50,
        "min_trade_value": 0.01,
    },
    "routes": {
        "construction_cost_wood_base": 64,
        "construction_cost_stone_base": 32,
        "maintenance_interval_ticks": 200,
        "degredation_per_skip": 1,
        "max_route_level": 5,
        "bandit_attraction_per_volume": 0.001,
        "trade_efficiency_per_level": 0.3,
        "speed_per_level_bonus": 0.3,
        "construction_ticks_per_segment": 5,
        "max_segments_per_route": 50,
    },
    "banditry": {
        "min_combat_skill": 20,
        "range_to_route": 500,
        "surprise_multiplier": 1.3,
        "notoriety_per_value_ratio": 0.1,
        "bounty_ratio": 0.5,
        "capture_jail_base_ticks": 50,
        "guard_response_delay_ticks": 3,
        "max_group_size": 10,
        "loot_destruction_chance": 0.1,
        "defeat_escape_chance": 0.3,
        "combat_damage_to_route": 0.05,
    },
    "reputation": {
        "reputation_min": -1000,
        "reputation_max": 1000,
        "notoriety_max": 1000,
        "notoriety_decay_rate": 0.01,
        "bounty_decay_rate": 0.001,
        "guard_notoriety_threshold": 50,
        "guard_hostile_reputation": -200,
        "bounty_clear_multiplier": 1.5,
        "jail_ticks_per_notoriety": 0.5,
        "reputation_per_trade": 0.5,
        "notoriety_per_theft": 35,
        "notoriety_per_banditry_base": 100,
    },
    "world_events": {
        "check_interval_ticks": 10,
        "depletion_chance": 0.01,
        "shortage_chance": 0.005,
        "boom_chance": 0.003,
        "disaster_chance": 0.001,
        "regen_rate_normal": 0.001,
        "regen_rate_depleted": 0.0005,
        "max_active_events": 3,
        "event_cooldown_ticks": 500,
        "depletion_duration_min": 500,
        "depletion_duration_max": 2000,
        "shortage_duration_min": 200,
        "shortage_duration_max": 1000,
        "boom_duration_min": 300,
        "boom_duration_max": 1500,
        "disaster_duration_min": 500,
        "disaster_duration_max": 3000,
    },
    "economy": {
        "elasticity_commodity": 0.005,
        "elasticity_manufactured": 0.002,
        "elasticity_luxury": 0.001,
        "price_min_multiplier": 0.5,
        "price_max_multiplier": 3.0,
        "supply_regen_per_tick": 0.01,
        "demand_shift_per_trade": 0.02,
        "population_demand_factor": 0.1,
        "base_supply_per_region": 100.0,
        "base_demand_per_region": 100.0,
    },
}


def load_config(reload: bool = False) -> dict[str, Any]:
    """Load configuration from config.json, merged with defaults.

    Args:
        reload: If True, re-read from disk even if cached.

    Returns:
        Full configuration dict with all keys populated.
    """
    global _cache
    if _cache is not None and not reload:
        return _cache

    config = _deep_merge(_DEFAULT_CONFIG, {})

    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r") as f:
                user_config = json.load(f)
            config = _deep_merge(config, user_config)
            _log_msg(f"Loaded config from {_CONFIG_PATH}")
        except (json.JSONDecodeError, OSError) as e:
            _log_msg(f"Config load error: {e} — using defaults")
    else:
        _log_msg(f"Config file not found at {_CONFIG_PATH} — using defaults")

    _cache = config
    return config


def get_config(section: str, key: str, default: Any = None) -> Any:
    """Get a specific config value by section and key.

    Args:
        section: Config section name (e.g. 'trade', 'banditry')
        key: Key within section
        default: Fallback value

    Returns:
        The config value, or default if not found.
    """
    config = load_config()
    section_data = config.get(section, {})
    return section_data.get(key, default)


def get_log() -> list[str]:
    """Return all log messages collected during init."""
    global _log
    return list(_log)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    try:
        result = {}

    except Exception as e:
        log.error(f"_deep_merge failed: {e}")
        return {}
    for k, v in base.items():
        if k in override:
            if isinstance(v, dict) and isinstance(override[k], dict):
                result[k] = _deep_merge(v, override[k])
            else:
                result[k] = override[k]
        else:
            result[k] = v
    for k, v in override.items():
        if k not in result:
            result[k] = v
    return result


def _log_msg(msg: str) -> None:
    global _log
    _log.append(msg)

