# Steam Nextcloud Status Updater API

Monitors Minecraft activity via the MC Status Relay (UDP 25568) and
updates Nextcloud user status automatically.

## How It Works
1. Queries the MC Status Relay every ~5 seconds for current biome/dim.
2. If the player is actively in Minecraft (<20s since last update):
   Sets Nextcloud status message to "Playing Minecraft in <biome> (<dim>)"
3. When the player leaves Minecraft:
   Restores the previous Nextcloud status (saved at startup).

## Hooks
None provided. Does not register with the engine hook system.

## Commands
None (this script runs autonomously).

## Configuration
All settings stored in `DATA/steam_nextcloud_config.ini` (centralized).
Legacy path `DATA/config.ini` (local to script directory) is auto-migrated on first load.
