# Auction House — Minescript Client

**`ah.py`** — The in-game `\ah` command for Minecraft players.

## Installation

```bash
cp ~/Documents/dev-yuniScripts/SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/HELPERS/ah.py ~/.minecraft/minescript/ah.py
```

In Minecraft, type `\ah help` to see all commands.

## Available Commands

| Command | Description |
|---------|-------------|
| `\ah list <price> [bin] [hours]` | List held item on the AH |
| `\ah bid <uuid> <amount>` | Place a bid |
| `\ah buy <uuid>` | Buy-It-Now |
| `\ah mine` | View your active listings |
| `\ah cancel <uuid>` | Cancel your listing |
| `\ah search [item]` | Search active listings |
| `\ah details <uuid>` | View full listing details (enchantments, lore, stats) |
| `\ah history <uuid>` | View bid history for a listing |
| `\ah purchases` | View items you've bought |
| `\ah sales` | View items you've sold |
| `\ah pricecheck <item>` | Check recent market prices (e.g. `\ah pricecheck coal`) |
| `\ah report` | Request weekly market report |
| `\ah help [command]` | Show help |

## Protocol

The client communicates via UDP to the Phooks hub on port 25573.
Events are automatically forwarded to the mc_manager which processes
them and returns a response.

## Logging

Client logs are stored in `~/.minecraft/minescript/ah_logs/ah_client_YYYY-MM-DD.log`

## Requirements

- Minescript mod (for Minecraft 1.21+)
- mc_manager running with Auction House enabled
- Phooks hub running on port 25573
