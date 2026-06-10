# Auction House Package
# Auction house system for mc_manager — player-driven economy with AI simulation.
# See AuctionHouse-spec.md for full specification.
#
# Submodules:
#   ah_config.py          — Configuration loading & defaults
#   ah_logger.py          — Structured logging (JSONL + plaintext)
#   ah_database.py        — DB connection manager & schema (7 tables)
#   ah_helper_db.py       — AI notes & categories management
#   ah_core.py            — Core auction CRUD (list, bid, buy, cancel, query)
#   ah_price_history.py   — Price tracking, snapshots, trend analysis
#   ah_market_events.py   — Market event system (start/end/check)
#   ah_item_gen.py        — Rare item generation, enchantments, lore
#   ah_announcer.py       — In-game RCON announcements (/tellraw)
#   ah_ai_engine.py       — DeepSeek API integration & simulation loop
#   ah_reports.py         — Weekly market reports
#   ah_phooks.py          — Phooks event handlers & dispatch
#   HELPERS/ah_protocol.py — Shared constants & message schemas
#   HELPERS/ah_commands.py — Minescript client command definitions
#
# Phase: Foundation + Core + AI Engine (all modules implemented)
# See logs/ for operational log, AI simulations, transactions, events

__version__ = "0.2.0"  # Foundation phase — all core modules complete
