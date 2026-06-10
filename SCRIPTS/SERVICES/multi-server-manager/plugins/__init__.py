"""
Multi-Server Manager — Subsystem Plugins
Phase 6: Ported existing subsystems to plugin architecture.

Each plugin:
  - Subclasses SubsystemPlugin with lifecycle hooks
  - Gets its own VFS-backed isolated database via VFSDatabaseManager
  - Is registered in the PluginRegistry singleton
  - Exposes the subsystem's API through clean async methods

Plugin Registration:
    from engine.plugin_registry import PluginRegistry
    from .auction_house_plugin import AuctionHousePlugin
    from .economy_bridge_plugin import EconomyBridgePlugin
    from .simulated_people_plugin import SimulatedPeoplePlugin
    from .lootpower_games_plugin import LootPowerGamesPlugin

    registry = PluginRegistry.get_instance()
    registry.register(AuctionHousePlugin)
    registry.register(EconomyBridgePlugin)
    registry.register(SimulatedPeoplePlugin)
    registry.register(LootPowerGamesPlugin)
"""

from .auction_house_plugin import AuctionHousePlugin
from .economy_bridge_plugin import EconomyBridgePlugin
from .simulated_people_plugin import SimulatedPeoplePlugin
from .lootpower_games_plugin import LootPowerGamesPlugin

__all__ = [
    "AuctionHousePlugin",
    "EconomyBridgePlugin",
    "SimulatedPeoplePlugin",
    "LootPowerGamesPlugin",
]

# Convenience: list of all plugin classes for batch registration
ALL_PLUGINS = [
    AuctionHousePlugin,
    EconomyBridgePlugin,
    SimulatedPeoplePlugin,
    LootPowerGamesPlugin,
]
