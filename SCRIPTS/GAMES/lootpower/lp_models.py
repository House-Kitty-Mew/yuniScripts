"""
Data models for LootPower entities.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Player:
    """A player in the LootPower system."""
    user_id: str
    user_name: str
    password: str = ""
    turns: int = 20
    global_loot_chance_boost: float = 0.0
    turn_time: float = 0.0
    is_active: bool = True


@dataclass
class LootItem:
    """A loot table entry (what CAN drop)."""
    loot: str                                       # name
    loot_chance: float                              # base drop denominator
    loot_chance_raise: float                        # chance raise on drop
    self_loot_chance_lower: float = 0.0            # per-owned reduction
    loot_lore: str = ""
    loot_id: int = 0


@dataclass
class UserLoot:
    """A player's inventory entry for one loot type."""
    user_id: str
    loot: str
    loot_id: int
    loot_amount: int = 0
    common: int = 0
    uncommon: int = 0
    rare: int = 0
    great: int = 0
    amazing: int = 0
    legendary: int = 0
    epic: int = 0
    godly: int = 0
    mythic: int = 0


@dataclass
class LootPower:
    """Player's lootpower score."""
    user_id: str
    lootpower: float = 0.0


@dataclass
class Story:
    """Adventure story text."""
    story_id: int = 0
    story_text: str = ""
    story_cat: str = ""
    creation_time: float = 0.0


@dataclass
class Stat:
    """Global stat tracking."""
    adventures: int = 0
    sold_items: int = 0


@dataclass
class LootStat:
    """Per-drop logging."""
    user_id: str = ""
    loot_name: str = ""
    loot_rarity: str = ""
    year: int = 0
    month: int = 0
    day: int = 0
    hour: int = 0
    minute: int = 0


@dataclass
class Auction:
    """Auction listing."""
    seller_id: str = ""
    loot_id: int = 0
    loot_amount: int = 0
    payment_type: int = 0
    payment_id: int = 0
    payment_amount: int = 0
    auction_id: int = 0


@dataclass
class CraftRecipe:
    """Crafting recipe (two items -> result)."""
    item_one_id: int = 0
    item_two_id: int = 0
    rep_item: str = ""
    rep_item_id: int = 0


@dataclass
class UserCraft:
    """Player's crafted items."""
    user_id: str = ""
    craft_id: int = 0
    amount: int = 0
    rarity: str = "common"


@dataclass
class MiningArea:
    """Mined zone claim."""
    zone_x: int = 0
    zone_y: int = 0
    user_id: str = ""
    ore: str = ""


@dataclass
class UserOre:
    """Player's mined ore inventory."""
    user_id: str = ""
    power_coin: int = 0
    bag_of_dirt: int = 0
    loot_ore: int = 0


@dataclass
class Watcher:
    """Observer/watcher session."""
    watcher_id: str
    label: str = ""
    filters: str = ""          # JSON filter string
    active: bool = True
    created_at: float = 0.0


@dataclass
class RuntimeCode:
    """Runtime control code."""
    name: str = "Server"
    code: int = 0