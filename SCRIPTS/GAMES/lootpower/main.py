#!/usr/bin/env python3
"""
LootPower — YuniScripts Game Engine (Main Entry Point)

CLI interface for:
  - Running the game server (UDP listener)
  - Admin commands (manage players, loot, etc.)
  - Watcher mode (observe without playing)
  - Turn replenishment daemon
  - Phooks event bus integration

Usage:
    python main.py server          # Start game server
    python main.py daemon          # Start turn replenishment daemon
    python main.py phooks          # Start phooks event bus only
    python main.py cli             # Interactive CLI
    python main.py watcher         # Watcher/observer session

    # One-shot commands:
    python main.py adventure <user_id> <show_story>
    python main.py mine <user_id> <zone_x> <zone_y>
    python main.py craft <user_id> <item1> <rar1> <item2> <rar2>
    python main.py profile <user_id>
    python main.py leaderboard
    python main.py loot-table
    python main.py add-loot <name> <chance> <raise> <lore>
    python main.py add-story <text> <category>
    python main.py replenish         # Manual turn replenish
"""
import sys
import time
import json
import random
import threading

import lp_config
from lp_database import get_db, DatabaseEngine
from lp_player import PlayerService
from lp_chance import LootChanceEngine
from lp_adventure import AdventureSystem
from lp_mining import MiningSystem
from lp_crafting import CraftingSystem
from lp_auction import AuctionHouse
from lp_leaderboard import Leaderboard
from lp_watcher import WatcherService
from lp_phooks import LootPowerPhooks


# ---------------------------------------------------------------------------
# CLI Command Router
# ---------------------------------------------------------------------------
class LootPowerCLI:
    """Command-line interface for LootPower."""

    def __init__(self):
        self.db = get_db()
        self.player_svc = PlayerService()
        self.chance_engine = LootChanceEngine()
        self.adventure_sys = AdventureSystem(self.player_svc, self.chance_engine)
        self.mining_sys = MiningSystem()
        self.crafting_sys = CraftingSystem()
        self.auction_house = AuctionHouse()
        self.leaderboard = Leaderboard()
        self.watcher_svc = WatcherService()
        self.phooks = LootPowerPhooks()

    def setup_phooks_hooks(self):
        """Wire up phooks event handlers to game systems."""

        def on_adventure_start(event, data, sender):
            self.phooks.log(f"Adventure started: {data}")

        def on_loot_dropped(event, data, sender):
            self.phooks.log(f"Loot dropped: {data.get('loot_name')} ({data.get('rarity')})")

        self.phooks.subscribe("lootpower:adventure_start", on_adventure_start)
        self.phooks.subscribe("lootpower:loot_dropped", on_loot_dropped)

        # Register action handlers for external scripts
        def handle_adventure(params, requester):
            uid = params.get("user_id", "")
            story = params.get("show_story", "1")
            # Emit start event
            self.phooks.emit("lootpower:adventure_start",
                             {"user_id": uid, "requester": requester})
            result = self.adventure_sys.adventure(uid, show_story=story)
            # Emit complete event
            self.phooks.emit("lootpower:adventure_end",
                             {"user_id": uid, "result": result})
            return result

        def handle_mine(params, requester):
            uid = params.get("user_id", "")
            zx = int(params.get("zone_x", 0))
            zy = int(params.get("zone_y", 0))
            result = self.mining_sys.mine(uid, zx, zy)
            self.phooks.emit("lootpower:mine_hit",
                             {"user_id": uid, "ore": result, "zone": (zx, zy)})
            return result

        def handle_craft(params, requester):
            uid = params.get("user_id", "")
            i1 = int(params.get("item_one", 0))
            r1 = int(params.get("rarity_one", 0))
            i2 = int(params.get("item_two", 0))
            r2 = int(params.get("rarity_two", 0))
            result = self.crafting_sys.craft(uid, i1, r1, i2, r2)
            self.phooks.emit("lootpower:craft_complete",
                             {"user_id": uid, "result": result})
            return result

        def handle_profile(params, requester):
            uid = params.get("user_id", "")
            return self.player_svc.get_profile(uid)

        def handle_leaderboard(params, requester):
            return self.leaderboard.get_rankings()

        def handle_system_stats(params, requester):
            return self.watcher_svc.get_system_stats()

        self.phooks.register_action("adventure", handle_adventure)
        self.phooks.register_action("mine", handle_mine)
        self.phooks.register_action("craft", handle_craft)
        self.phooks.register_action("profile", handle_profile)
        self.phooks.register_action("leaderboard", handle_leaderboard)
        self.phooks.register_action("system_stats", handle_system_stats)
        self.phooks.register_action("replenish_turns",
                                     lambda p, r: self.player_svc.replenish_turns())

    def cmd_adventure(self, args):
        """Perform an adventure. Usage: adventure <user_id> [show_story]"""
        if len(args) < 1:
            return "Usage: adventure <user_id> [show_story=1]"
        uid = args[0]
        story = args[1] if len(args) > 1 else "1"
        result = self.adventure_sys.adventure(uid, show_story=story)
        # Emit phooks event
        self.phooks.emit("lootpower:loot_dropped",
                         {"user_id": uid, "result": result})
        return result

    def cmd_mine(self, args):
        """Mine a zone. Usage: mine <user_id> <zone_x> <zone_y>"""
        if len(args) < 3:
            return "Usage: mine <user_id> <zone_x> <zone_y>"
        return self.mining_sys.mine(args[0], int(args[1]), int(args[2]))

    def cmd_craft(self, args):
        """Craft items. Usage: craft <user_id> <item1> <rar1> <item2> <rar2>"""
        if len(args) < 5:
            return "Usage: craft <user_id> <item1_id> <rar1_idx> <item2_id> <rar2_idx>"
        return self.crafting_sys.craft(args[0], int(args[1]), int(args[2]),
                                        int(args[3]), int(args[4]))

    def cmd_profile(self, args):
        """Get player profile. Usage: profile <user_id>"""
        if len(args) < 1:
            return "Usage: profile <user_id>"
        profile = self.player_svc.get_profile(args[0])
        if not profile:
            return f"Player {args[0]} not found"
        return json.dumps(profile, indent=2)

    def cmd_leaderboard(self, args):
        """Show leaderboard."""
        rankings = self.leaderboard.get_rankings()
        lines = ["  #  | Name                        | Score"]
        lines.append("-" * 55)
        for r in rankings:
            lines.append(f"  {r['rank']:2d}  | {r['name']:27s} | {r['score']:.2f}")
        return "\n".join(lines[:30])

    def cmd_loot_table(self, args):
        """Show the loot table."""
        items = self.chance_engine.get_all_loot_items()
        lines = ["ID  | Item                  | Base Chance | Raise   | Lore"]
        lines.append("-" * 70)
        for item in items:
            lines.append(
                f"{item['loot_id']:3d} | {item['loot']:21s} | "
                f"{float(item['loot_chance']):>8.4f}    | "
                f"{float(item['loot_chance_raise']):>6.4f} | "
                f"{item.get('loot_lore', '')[:20]}"
            )
        return "\n".join(lines)

    def cmd_add_loot(self, args):
        """Add loot item. Usage: add-loot <name> <chance> <raise> <lore>"""
        if len(args) < 3:
            return "Usage: add-loot <name> <chance> <raise> [lore]"
        name = args[0]
        chance = float(args[1])
        raise_val = float(args[2])
        lore = args[3] if len(args) > 3 else ""
        self_lower = raise_val * 10
        self.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, self_loot_chance_lower, loot_lore) VALUES (?,?,?,?,?)",
            (name, chance, raise_val, self_lower, lore)
        )
        self.db.commit()
        return f"Added loot: {name} (chance={chance}, raise={raise_val})"

    def cmd_add_story(self, args):
        """Add story. Usage: add-story <text> <category>"""
        if len(args) < 1:
            return "Usage: add-story <text> [category=general]"
        text = args[0]
        cat = args[1] if len(args) > 1 else "general"
        self.db.execute(
            "INSERT INTO story_table (story_text, story_cat, creation_time) VALUES (?,?,?)",
            (text, cat, time.time())
        )
        self.db.commit()
        return f"Added story: {text[:50]}..."

    def cmd_replenish(self, args):
        """Replenish all players' turns."""
        count = self.player_svc.replenish_turns()
        self.phooks.emit("lootpower:turn_replenish",
                         {"count": count, "time": time.time()})
        return f"Replenished turns for {count} players"

    def cmd_create_user(self, args):
        """Create player. Usage: create-user <user_id> <username> <password>"""
        if len(args) < 3:
            return "Usage: create-user <user_id> <username> <password>"
        ok = self.player_svc.create_user(args[0], args[1], args[2])
        return f"User {args[1]} created" if ok else "Failed to create user"

    def cmd_system_stats(self, args):
        """Show system stats."""
        return json.dumps(self.watcher_svc.get_system_stats(), indent=2)

    def cmd_watcher_register(self, args):
        """Register a watcher. Usage: watcher-register <id> [label]"""
        wid = args[0]
        label = args[1] if len(args) > 1 else ""
        ok = self.watcher_svc.register_watcher(wid, label)
        return f"Watcher {wid} registered" if ok else "Failed to register"

    def cmd_watcher_unregister(self, args):
        """Unregister a watcher."""
        wid = args[0]
        ok = self.watcher_svc.unregister_watcher(wid)
        return f"Watcher {wid} deactivated" if ok else "Failed"

    def run_command(self, cmd: str, args: list) -> str:
        """Route a command to the appropriate handler."""
        handler_map = {
            "adventure": self.cmd_adventure,
            "mine": self.cmd_mine,
            "craft": self.cmd_craft,
            "profile": self.cmd_profile,
            "leaderboard": self.cmd_leaderboard,
            "loot-table": self.cmd_loot_table,
            "add-loot": self.cmd_add_loot,
            "add-story": self.cmd_add_story,
            "replenish": self.cmd_replenish,
            "create-user": self.cmd_create_user,
            "system-stats": self.cmd_system_stats,
            "watcher-register": self.cmd_watcher_register,
            "watcher-unregister": self.cmd_watcher_unregister,
        }
        handler = handler_map.get(cmd)
        if handler:
            return handler(args)
        return f"Unknown command: {cmd}. Available: {', '.join(sorted(handler_map.keys()))}"

    def run_server_mode(self):
        """Run the game server with all services."""
        self.setup_phooks_hooks()
        self.phooks.start()

        print(f"=== LootPower Server v2.0 (YuniScripts) ===")
        print(f"Phooks port: {self.phooks.port}")
        print(f"DB: {lp_config.DB_PATH}")

        # Start turn daemon
        def turn_daemon_loop():
            ticks = 0
            while True:
                time.sleep(lp_config.TURN_REPLENISH_INTERVAL)
                count = self.player_svc.replenish_turns()
                ticks += 1
                self.phooks.emit("lootpower:turn_replenish",
                                 {"count": count, "tick": ticks})
                print(f"[Tick {ticks}] Replenished {count} players")

        td = threading.Thread(target=turn_daemon_loop, daemon=True)
        td.start()
        print("Turn daemon started (60s interval)")

        # CLI input loop
        print("\nEnter commands (or 'help'):")
        while True:
            try:
                line = input("lp> ").strip()
                if not line:
                    continue
                if line == "quit" or line == "exit":
                    break
                if line == "help":
                    print("Commands: adventure, mine, craft, profile, leaderboard,")
                    print("  loot-table, replenish, create-user, system-stats,")
                    print("  add-loot, add-story, watcher-register, watcher-unregister")
                    print("  quit/exit")
                    continue

                parts = line.split()
                cmd = parts[0]
                args_list = parts[1:]
                result = self.run_command(cmd, args_list)
                print(result)

            except KeyboardInterrupt:
                print("\nShutting down...")
                break
            except Exception as e:
                print(f"Error: {e}")

        self.phooks.stop()
        print("Server stopped.")

    def run_daemon_mode(self):
        """Run only the turn replenishment daemon."""
        print(f"Turn daemon running (interval={lp_config.TURN_REPLENISH_INTERVAL}s)")
        ticks = 0
        while True:
            time.sleep(lp_config.TURN_REPLENISH_INTERVAL)
            count = self.player_svc.replenish_turns()
            ticks += 1
            print(f"[Tick {ticks}] Replenished {count} players")

    def run_phooks_mode(self):
        """Run only the phooks event bus."""
        self.setup_phooks_hooks()
        self.phooks.start()
        print(f"Phooks event bus running on port {self.phooks.port}")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        self.phooks.stop()

    def run_watcher_mode(self):
        """Interactive watcher session."""
        print("=== LootPower Watcher Mode ===")
        print("Observing system state (read-only)...\n")
        print("Commands: stats, loot-table, leaderboard, recent-drops, help, quit")
        while True:
            try:
                line = input("watcher> ").strip()
                if not line:
                    continue
                if line in ("quit", "exit"):
                    break
                if line == "help":
                    print("stats, loot-table, leaderboard, recent-drops, help, quit")
                    continue

                if line == "stats":
                    print(json.dumps(self.watcher_svc.get_system_stats(), indent=2))
                elif line == "loot-table":
                    items = self.watcher_svc.get_loot_table()
                    for item in items:
                        print(f"  #{item['loot_id']}: {item['loot']} "
                              f"(chance={item['loot_chance']})")
                elif line == "leaderboard":
                    print(self.cmd_leaderboard([]))
                elif line == "recent-drops":
                    drops = self.watcher_svc.get_recent_drops(10)
                    for d in drops:
                        print(f"  {d.get('user_name','?'):20s} "
                              f"found {d['loot_rarity']:10s} {d['loot_name']}")
                else:
                    print(f"Unknown: {line}")
            except KeyboardInterrupt:
                break

    def run_cli_mode(self):
        """Interactive CLI mode."""
        print("=== LootPower CLI ===")
        print("Type 'help' for commands, 'quit' to exit.\n")
        while True:
            try:
                line = input("lp> ").strip()
                if not line:
                    continue
                if line in ("quit", "exit"):
                    break
                if line == "help":
                    print("Commands:", ", ".join([
                        "adventure", "mine", "craft", "profile",
                        "leaderboard", "loot-table", "add-loot",
                        "add-story", "replenish", "create-user",
                        "system-stats", "watcher-register",
                        "watcher-unregister"
                    ]))
                    continue
                parts = line.split()
                cmd = parts[0]
                args_list = parts[1:]
                print(self.run_command(cmd, args_list))
            except KeyboardInterrupt:
                break


def main():
    cli = LootPowerCLI()

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    args = sys.argv[2:]

    if mode == "server":
        cli.run_server_mode()
    elif mode == "daemon":
        cli.run_daemon_mode()
    elif mode == "phooks":
        cli.run_phooks_mode()
    elif mode == "watcher":
        cli.run_watcher_mode()
    elif mode == "cli":
        cli.run_cli_mode()
    else:
        # One-shot command
        print(cli.run_command(mode, args))


if __name__ == "__main__":
    main()