# Economy Bridge — Otters Civ Integration

Bridges the Auction House system with the **Otters Civ Revived** economy mod by reading/writing `project_ooga.db` directly.

## Architecture

```
AH System (ah_core.py)
    │
    ├── RCON (primary): /money add/take/set commands
    │   └── Works when server is running normally
    │
    └── Economy Bridge (ECO_BRIDGE/eco_bridge.py)
        └── Direct SQLite access to config/otters_civ_revived/project_ooga.db
            ├── Read balances instantly (no RCON round-trip)
            ├── Write balances (fallback when RCON fails)
            └── Immutable wallet_ledger audit trail
```

## How It Works

### Balance Reads
- Always from the SQLite database directly (instant, no RCON)
- Tries UUID → player name → case-insensitive name lookup
- Returns `None` if player not found

### Balance Writes
1. **Try RCON first** — sends `/money take/add/set` command to server
2. **Fallback to DB** — if RCON fails, writes directly to `wallets` and `wallet_ledger` tables
3. **Wallet ledger entries** — all DB writes include `reason: "AH_BRIDGE:..."` for full audit trail

### Safety Limits
- Max per-transaction deduction: 100,000 coins (configurable)
- Min balance enforced: 0 (configurable)
- Negative balances prevented

## AH Integration Points

| Operation | Bridge Check | Bridge Write |
|-----------|-------------|-------------|
| `\ah list` | Checks player has enough for listing fee | Deducts listing fee |
| `\ah bid` | Checks bidder has enough coins | — |
| `\ah buy` (BIN) | Checks buyer can afford total | Deducts from buyer, credits seller |
| `\ah cancel` (with bids) | — | Charges cancel fee |

## Configuration

Edit `ECO_BRIDGE/eco_config.json` (now at `DATA/eco_config.json`):

```json
{
    "ooga_db_path": "config/otters_civ_revived/project_ooga.db",
    "rcon_primary": true,
    "rcon_timeout_seconds": 3.0,
    "max_delta_per_transaction": 100000,
    "min_balance": 0
}
```

If `ooga_db_path` is empty, the bridge auto-searches:
1. `~/minecraft_server/config/otters_civ_revived/project_ooga.db`
2. Other common server directories
3. Home directory fallback paths

## Standalone Mode

The bridge can run as its own Phooks client:

```bash
cd ~/Documents/dev-yuniScripts/SCRIPTS/GAMES/minecraft_manager
python3 -m ECO_BRIDGE.eco_bridge
```

This registers with the Phooks hub and handles `economy_*` events independently.
Useful for debugging or running the economy bridge on a separate machine.

## Phooks Events

| Request | Response | Description |
|---------|----------|-------------|
| `economy_balance` | `economy_balance_response` | Get player balance |
| `economy_deduct` | `economy_deduct_response` | Deduct coins |
| `economy_credit` | `economy_credit_response` | Add coins |
| `economy_transfer` | `economy_transfer_response` | Transfer between players |
| `economy_set` | `economy_set_response` | Set exact balance |
| `economy_ledger` | `economy_ledger_response` | View audit trail |
| `economy_stats` | `economy_stats_response` | Economy statistics |

## Logs

`ECO_BRIDGE/logs/eco_bridge.log` — all operations, RCON successes/failures, DB writes.
