# ah.py — Minescript Auction House Client
# Drop this file into ~/.minecraft/minescript/
# Then type \ah in-game to interact with the Auction House.
#
# Commands:
#   \ah list <start_price> [buy_now_price] [duration_h]
#   \ah bid <listing_uuid> <amount>
#   \ah buy <listing_uuid>
#   \ah mine
#   \ah cancel <listing_uuid>
#   \ah search [item_name]
#   \ah report
#   \ah details <listing_uuid>
#   \ah history <listing_uuid>
#   \ah purchases
#   \ah sales
#   \ah pricecheck <item_id>
#   \ah help [command]
#
# Architecture:
#   minescript (this file) --UDP:_PHOOKS_PORT--> Phooks Hub --UDP--> mc_manager (ah_phooks.py)

import os, sys, json, socket, time, uuid, traceback, threading, queue
from datetime import datetime

# Port constants — local defines (minescript runs in Minecraft embedded Python)
_PHOOKS_PORT = 25573  # matches engine.ports.PHOOKS_HUB_PORT

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "ah_logs")
os.makedirs(LOG_DIR, exist_ok=True)

try:
    import minescript
except ImportError:
    print("This script must be run inside Minecraft with the Minescript mod installed.")
    sys.exit(1)

CONFIG = {
    "phooks_host": "127.0.0.1",
    "phooks_port": _PHOOKS_PORT,
    "udp_timeout": 3.0,
    "script_id": "minescript_ah_client",
    "log_enabled": True,
}


def log(level, msg):
    if not CONFIG["log_enabled"]:
        return
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = os.path.join(LOG_DIR, f"ah_client_{today}.log")
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{level}] {msg}\n")
    except Exception:
        pass


class AHClient:
    """Phooks client that registers with the hub and handles request/response.

    Key difference from the old version: this now properly REGISTERs with the
    Phooks hub, so the hub knows how to route responses back to this client.
    Uses a background receive thread and event queue for reliable delivery.
    """

    # Events this client sends to the hub
    _EMIT_EVENTS = ["ah_list", "ah_bid", "ah_buy", "ah_remove", "ah_query", "ah_test", "ah_games"]
    # Events this client receives from the hub (responses + broadcasts)
    _LISTEN_EVENTS = ["ah_list_response", "ah_bid_response", "ah_buy_response",
                      "ah_remove_response", "ah_query_response", "ah_test_response",
                      "ah_announce",
                      "ah_games_response"]

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))  # Bind to random port — hub needs to reply to us
        self.sock.settimeout(1.0)
        self.event_queue = queue.Queue()
        self._running = False
        self._thread = None
        self._register()

    def _register(self):
        """Register this client with the Phooks hub.

        The hub learns our address from the UDP source port and will forward
        any EMIT events we're listening for to this socket.
        """
        msg = json.dumps({
            "command": "REGISTER",
            "script_id": CONFIG["script_id"],
            "listen_events": self._LISTEN_EVENTS,
            "emit_events": self._EMIT_EVENTS,
        }).encode()
        try:
            self.sock.sendto(msg, (CONFIG["phooks_host"], CONFIG["phooks_port"]))
        except Exception as e:
            minescript.echo(f"§c[AH] Registration failed: {e}")
            return
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        log("INFO", f"Registered with Phooks hub at {CONFIG['phooks_host']}:{CONFIG['phooks_port']}")

    def _receive_loop(self):
        """Background thread: receives all incoming UDP and queues matching events."""
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65535)
                msg = json.loads(data.decode("utf-8"))
                # Hub may send non-dict acknowledgments ("registered") — skip them
                if not isinstance(msg, dict) or "event" not in msg:
                    log("INFO", f"Hub message (non-event): {str(msg)[:100]}")
                    continue
                event_name = msg["event"]
                if event_name.startswith("ah_") or event_name.startswith("economy_"):
                    log("RECV", f"{event_name}")
                    self.event_queue.put(msg)
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as e:
                log("ERROR", f"Receive error: {e}")
                continue  # Don't crash the thread — keep listening

    def send(self, event, data):
        """Send an EMIT command to the Phooks hub."""
        payload = json.dumps({
            "command": "EMIT",
            "event": event,
            "data": data,
            "sender": CONFIG["script_id"],
        }).encode()
        try:
            self.sock.sendto(payload, (CONFIG["phooks_host"], CONFIG["phooks_port"]))
            log("SEND", f"{event}: {json.dumps(data)[:200]}")
        except Exception as e:
            minescript.echo(f"§c[AH] Network error: {e}")

    def send_and_wait(self, event, data, response_event, timeout=3.0):
        """Send an event and wait for a matching response from the hub.

        The hub forwards the server's EMIT as:
            {"command": "EMIT", "event": "ah_*_response",
             "data": {"request_uuid": "...", "data": {"status": "ok", ...}}}

        We unwrap the inner data so the handlers get:
            {"status": "ok", "message": "...", ...}
        """
        self.send(event, data)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.event_queue.get(timeout=0.2)
                event_name = msg.get("event", "")
                if event_name == response_event:
                    log("RECV", f"{response_event}: matched")
                    # Unwrap: msg.data holds the server's emit payload
                    payload = msg.get("data", {})
                    if isinstance(payload, dict) and "data" in payload:
                        return payload["data"]  # The actual result
                    return payload
            except queue.Empty:
                continue
            except Exception as e:
                log("ERROR", f"send_and_wait error: {e}")
                break
        log("WARN", f"Timeout waiting for {response_event} after {timeout}s")
        return None

    def close(self):
        """Stop the receive thread and close the socket."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.sock.close()


# ── Command helpers ──────────────────────────────────────────────

def _shorten_uuid(u):
    return u[:8] if u and len(u) > 8 else u or "?"

def _friendly_item(item_id):
    if not item_id:
        return "?"
    if ":" in item_id:
        return item_id.split(":")[1]
    return item_id


# ── Command handlers ─────────────────────────────────────────────

def cmd_list(args, client):
    """\ah list <start_price> [buy_now_price] [duration_h]"""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah list <start_price> [buy_now_price] [duration_h]")
        return
    player = minescript.player_name()
    hands = minescript.player_hand_items()
    main = hands.main_hand
    off = hands.off_hand
    if not main or main["item"] == "minecraft:air":
        minescript.echo("§c[AH] Hold the item you want to list in your main hand.")
        return
    if not off or off["item"] != "minecraft:emerald":
        minescript.echo("§c[AH] Put an emerald in your off-hand as payment.")
        return
    try:
        start_price = float(args[0])
        buy_now_price = float(args[1]) if len(args) > 1 else None
        duration_h = int(args[2]) if len(args) > 2 else None
    except ValueError:
        minescript.echo("§c[AH] Invalid price. Use numbers only.")
        return
    item_id = main["item"]
    count = main["count"]
    nbt = main.get("nbt", "") if isinstance(main, dict) else ""
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "item_id": item_id, "item_count": count, "item_nbt": nbt,
            "start_price": start_price, "buy_now_price": buy_now_price,
            "duration_hours": duration_h}
    minescript.echo(f"§e[AH] Listing {count}x {item_id} for {start_price} emeralds...")
    resp = client.send_and_wait("ah_list", data, "ah_list_response")
    if resp:
        if resp.get("status") == "ok":
            minescript.echo(f"§a[AH] {resp.get('message', 'Listed!')}")
        else:
            minescript.echo(f"§c[AH] {resp.get('message', 'Failed.')}")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_bid(args, client):
    """\ah bid <listing_uuid> <amount>"""
    if len(args) < 2:
        minescript.echo("§cUsage: \\ah bid <listing_uuid> <amount>")
        return
    listing_uuid = args[0]
    try:
        amount = float(args[1])
    except ValueError:
        minescript.echo("§c[AH] Invalid bid amount.")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "listing_uuid": listing_uuid, "bid_amount": amount}
    minescript.echo(f"§e[AH] Bidding {amount} emeralds...")
    resp = client.send_and_wait("ah_bid", data, "ah_bid_response")
    if resp:
        if resp.get("status") == "ok":
            minescript.echo(f"§a[AH] {resp.get('message', 'Bid placed!')}")
        else:
            minescript.echo(f"§c[AH] {resp.get('message', 'Bid failed.')}")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_buy(args, client):
    """\ah buy <listing_uuid>"""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah buy <listing_uuid>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "listing_uuid": args[0], "quantity": 1}
    minescript.echo("§e[AH] Buying item...")
    resp = client.send_and_wait("ah_buy", data, "ah_buy_response")
    if resp:
        if resp.get("status") == "ok":
            minescript.echo(f"§a[AH] {resp.get('message', 'Purchased!')}")
        else:
            minescript.echo(f"§c[AH] {resp.get('message', 'Failed.')}")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_mine(args, client):
    """\ah mine — Show your active listings."""
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "filter_type": "my", "filter_value": player}
    minescript.echo("§e[AH] Fetching your listings...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        listings = resp.get("listings", [])
        if not listings:
            minescript.echo("§7[AH] You have no active listings.")
            return
        minescript.echo(f"§6═══ §eYour Listings ({len(listings)}) §6═══")
        for idx, item in enumerate(listings[:10]):
            item_id = _friendly_item(item.get("item_id"))
            price = item.get("current_bid") or item.get("start_price", 0)
            bin_p = item.get("buy_now_price")
            bids = item.get("bids_count", 0)
            u = _shorten_uuid(item.get("listing_uuid"))
            line = f" §7{idx+1}. §f{item_id} §7- §e{price}em"
            if bin_p:
                line += f" §7BIN: §e{bin_p}em"
            line += f" §7| {bids} bids §7| §8{u}"
            minescript.echo(line)
        if len(listings) > 10:
            minescript.echo(f" §7... and {len(listings)-10} more.")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_cancel(args, client):
    """\ah cancel <listing_uuid>"""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah cancel <listing_uuid>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "listing_uuid": args[0]}
    minescript.echo("§e[AH] Cancelling listing...")
    resp = client.send_and_wait("ah_remove", data, "ah_remove_response")
    if resp:
        if resp.get("status") == "ok":
            minescript.echo(f"§a[AH] {resp.get('message', 'Cancelled!')}")
        else:
            minescript.echo(f"§c[AH] {resp.get('message', 'Cancel failed.')}")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_search(args, client):
    """\ah search [item_name] — Search active listings."""
    filter_value = " ".join(args) if args else ""
    player = minescript.player_name()
    if filter_value:
        data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
                "filter_type": "item", "filter_value": filter_value}
    else:
        data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
                "filter_type": "all", "filter_value": ""}
    minescript.echo(f"§e[AH] Searching{' for ' + filter_value if filter_value else ' all'}...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        listings = resp.get("listings", [])
        total = resp.get("total", 0)
        if not listings:
            minescript.echo(f"§7[AH] No active listings{' for ' + filter_value if filter_value else ''}.")
            return
        minescript.echo(f"§6═══ §eAuction House ({total} total) §6═══")
        for idx, item in enumerate(listings[:15]):
            item_id = _friendly_item(item.get("item_id"))
            price = item.get("current_bid") or item.get("start_price", 0)
            bin_p = item.get("buy_now_price")
            seller = item.get("seller_name", "?")
            bids = item.get("bids_count", 0)
            u = _shorten_uuid(item.get("listing_uuid"))
            sim_tag = " §7[AI]" if item.get("is_simulated") else ""
            line = f" §7{idx+1}. §f{item_id}{sim_tag} §7- §e{price}em"
            if bin_p:
                line += f" §7BIN: §e{bin_p}em"
            line += f" §7| {bids} bids §7| §8{seller}"
            minescript.echo(line)
        if total > 15:
            minescript.echo(f" §7... and {total-15} more.")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_details(args, client):
    """\ah details <listing_uuid> — View full listing details."""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah details <listing_uuid>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "filter_type": "details", "filter_value": args[0]}
    minescript.echo("§e[AH] Fetching listing details...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        listing = resp.get("listing")
        if not listing:
            minescript.echo("§c[AH] Listing not found.")
            return
        item_id = _friendly_item(listing.get("item_id"))
        signed_name = listing.get("signed_name") or item_id
        seller = listing.get("seller_name", "?")
        price = listing.get("current_bid") or listing.get("start_price", 0)
        bin_p = listing.get("buy_now_price")
        bids = listing.get("bids_count", 0)
        status = listing.get("status", "?")
        listed = listing.get("listed_at", "?")[:16] if listing.get("listed_at") else "?"
        expires = listing.get("expires_at", "?")[:16] if listing.get("expires_at") else "?"
        rarity = listing.get("rarity", "")
        is_sim = listing.get("is_simulated")
        lore = listing.get("sim_lore", "")
        enchants = listing.get("sim_enchantments", "")
        durability = listing.get("sim_durability")

        minescript.echo(f"§6═══ §e{item_id} §6═══")
        minescript.echo(f" §7Name: §f{signed_name}")
        if rarity:
            minescript.echo(f" §7Rarity: §f{rarity}")
        minescript.echo(f" §7Seller: §f{seller}{' §8[AI]' if is_sim else ''}")
        minescript.echo(f" §7Status: §f{status}")
        minescript.echo(f" §7Price: §e{price:.2f}em")
        if bin_p:
            minescript.echo(f" §7BIN: §e{bin_p:.2f}em")
        minescript.echo(f" §7Bids: §f{bids}")
        minescript.echo(f" §7Listed: §8{listed}")
        minescript.echo(f" §7Expires: §8{expires}")
        time_left = listing.get("time_remaining")
        if time_left:
            color = "§a" if time_left not in ("Ended", "<1m") else "§c"
            minescript.echo(f" §7Time left: {color}{time_left}")
        if lore:
            try:
                lore_list = json.loads(lore) if isinstance(lore, str) else lore
                if isinstance(lore_list, list):
                    for l in lore_list[:3]:
                        minescript.echo(f" §5{l[:80]}")
            except (json.JSONDecodeError, TypeError):
                minescript.echo(f" §5{lore[:100]}")
        if enchants:
            try:
                ench_list = json.loads(enchants) if isinstance(enchants, str) else enchants
                if isinstance(ench_list, list) and len(ench_list) > 0:
                    minescript.echo(f" §7Enchantments:")
                    for e in ench_list[:5]:
                        eid = _friendly_item(e.get("id", "?"))
                        minescript.echo(f"  §b{e['level']} {eid}")
            except (json.JSONDecodeError, TypeError):
                pass
        if durability is not None:
            minescript.echo(f" §7Durability: §f{durability}%")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_history(args, client):
    """\ah history <listing_uuid> — View bid history."""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah history <listing_uuid>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "filter_type": "history", "filter_value": args[0]}
    minescript.echo("§e[AH] Fetching bid history...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        history = resp.get("history", [])
        if not history:
            minescript.echo("§7[AH] No bids on this listing yet.")
            return
        minescript.echo(f"§6═══ §eBid History ({len(history)}) §6═══")
        for idx, tx in enumerate(history[:15]):
            bidder = tx.get("actor_name", "?")
            amount = tx.get("price", 0)
            ts = tx.get("created_at", "?")[:16] if tx.get("created_at") else "?"
            minescript.echo(f" §7{idx+1}. §f{bidder} §7- §e{amount:.2f}em §7@ §8{ts}")
        if len(history) > 15:
            minescript.echo(f" §7... and {len(history)-15} more.")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_purchases(args, client):
    """\ah purchases — View items you've bought."""
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "filter_type": "purchases"}
    minescript.echo("§e[AH] Fetching your purchases...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        purchases = resp.get("purchases", [])
        if not purchases:
            minescript.echo("§7[AH] You haven't bought anything yet.")
            return
        minescript.echo(f"§6═══ §eYour Purchases ({len(purchases)}) §6═══")
        for idx, tx in enumerate(purchases[:10]):
            item_id = _friendly_item(tx.get("item_id") or tx.get("orig_item_id"))
            price = tx.get("price", 0)
            ts = tx.get("created_at", "?")[:16] if tx.get("created_at") else "?"
            seller = tx.get("seller_name", "?")
            minescript.echo(f" §7{idx+1}. §f{item_id} §7- §e{price:.2f}em §7from §f{seller} §7@ §8{ts}")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_sales(args, client):
    """\ah sales — View items you've sold."""
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "filter_type": "sales"}
    minescript.echo("§e[AH] Fetching your sales...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        sales = resp.get("sales", [])
        if not sales:
            minescript.echo("§7[AH] You haven't sold anything yet.")
            return
        minescript.echo(f"§6═══ §eYour Sales ({len(sales)}) §6═══")
        for idx, item in enumerate(sales[:10]):
            item_id = _friendly_item(item.get("item_id"))
            price = item.get("sold_price", 0)
            buyer_name = item.get("highest_bidder", "?")
            ts = item.get("sold_at", "?")[:16] if item.get("sold_at") else "?"
            minescript.echo(f" §7{idx+1}. §f{item_id} §7- §e{price:.2f}em §7to §f{buyer_name} §7@ §8{ts}")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_pricecheck(args, client):
    """\ah pricecheck <item_id> — Check recent prices for an item."""
    if len(args) < 1:
        minescript.echo("§cUsage: \\ah pricecheck <item_id> (e.g. \\ah pricecheck coal)")
        return
    item_id = args[0]
    if ":" not in item_id:
        item_id = f"minecraft:{item_id}"
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "filter_type": "pricecheck", "filter_value": item_id}
    minescript.echo(f"§e[AH] Checking prices for {_friendly_item(item_id)}...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        prices = resp.get("prices", [])
        item_name = _friendly_item(resp.get("item_id", item_id))
        if not prices:
            minescript.echo(f"§7[AH] No recent sales for {item_name}.")
            return
        avg_price = sum(p.get("price", 0) for p in prices) / len(prices)
        min_price = min(p.get("price", 0) for p in prices)
        max_price = max(p.get("price", 0) for p in prices)
        minescript.echo(f"§6═══ §ePrice Check: {item_name} §6═══")
        minescript.echo(f" §7Sales (7 days): §f{len(prices)}")
        minescript.echo(f" §7Average: §e{avg_price:.2f}em")
        minescript.echo(f" §7Min: §e{min_price:.2f}em")
        minescript.echo(f" §7Max: §e{max_price:.2f}em")
        if len(prices) <= 5:
            for p in prices:
                ts = p.get("created_at", "?")[:10]
                minescript.echo(f"  §8{ts}: {p.get('price', 0):.2f}em")
        minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_report(args, client):
    """\ah report — Request the weekly market report."""
    data = {"request_uuid": str(uuid.uuid4()), "player_name": minescript.player_name(),
            "filter_type": "report"}
    minescript.echo("§e[AH] Requesting market report...")
    resp = client.send_and_wait("ah_query", data, "ah_query_response")
    if resp:
        chat_lines = resp.get("chat_lines", [])
        if chat_lines:
            for line in chat_lines[:20]:
                minescript.echo(line)
        else:
            report = resp.get("report", {})
            ov = report.get("overview", {})
            minescript.echo("§6═══ §eMarket Report §6═══")
            minescript.echo(f"§7Transactions: §f{ov.get('total_transactions', 0)}")
            minescript.echo(f"§7Volume: §f{ov.get('total_volume', 0):.2f}em")
            minescript.echo(f"§7Active: §f{ov.get('active_listings', 0)}")
            minescript.echo("§6═══════════════════")
    else:
        minescript.echo("§c[AH] Request timed out.")


def cmd_help(args, client):
    """\ah help [command] — Show help."""
    command = args[0].lower() if args else ""

    help_texts = {
        "list": [
            "§6[AH] §e\\ah list <start_price> [buy_now_price] [duration_h]",
            "§7List the item in your main hand on the Auction House.",
            "§7Hold item in main hand, emerald in off-hand.",
            "§8\\ah list 10",
            "§8\\ah list 10 25",
            "§8\\ah list 10 25 48",
        ],
        "bid": [
            "§6[AH] §e\\ah bid <listing_uuid> <amount>",
            "§7Place a bid. Must exceed current bid by 10%.",
            "§8\\ah bid abc12345 15.5",
        ],
        "buy": [
            "§6[AH] §e\\ah buy <listing_uuid>",
            "§7Buy item immediately at its BIN price.",
            "§8\\ah buy abc12345",
        ],
        "mine": ["§6[AH] §e\\ah mine", "§7View your active listings."],
        "cancel": [
            "§6[AH] §e\\ah cancel <listing_uuid>",
            "§7Cancel one of your listings.",
            "§8\\ah cancel abc12345",
        ],
        "search": [
            "§6[AH] §e\\ah search [item_name]",
            "§7Search active listings.",
            "§8\\ah search",
            "§8\\ah search diamond",
        ],
        "details": [
            "§6[AH] §e\\ah details <listing_uuid>",
            "§7View full details of a listing (enchantments, lore, price).",
            "§8\\ah details abc12345",
        ],
        "history": [
            "§6[AH] §e\\ah history <listing_uuid>",
            "§7View bid history for a listing.",
            "§8\\ah history abc12345",
        ],
        "purchases": [
            "§6[AH] §e\\ah purchases",
            "§7View items you've bought.",
        ],
        "sales": [
            "§6[AH] §e\\ah sales",
            "§7View items you've sold.",
        ],
        "pricecheck": [
            "§6[AH] §e\\ah pricecheck <item>",
            "§7Check recent prices for an item.",
            "§8\\ah pricecheck coal",
            "§8\\ah pricecheck minecraft:diamond",
        ],
        "report": ["§6[AH] §e\\ah report", "§7Weekly market report from the AI."],
        "test": ["§6[AH] §e\\ah test", "§7Run full system test (read-only).",
                  "§7Checks: DB, Economy Bridge, RCON, AI Scheduler, Events."],
    }

    if command and command in help_texts:
        for line in help_texts[command]:
            minescript.echo(line)
        return

    # General help
    minescript.echo("§6═══ §eAuction House Commands §6═══")
    all_cmds = [
        ("list", "List item on AH"),
        ("bid", "Bid on listing"),
        ("buy", "Buy-It-Now"),
        ("mine", "Your listings"),
        ("cancel", "Cancel listing"),
        ("search", "Search listings"),
        ("details", "Listing details"),
        ("history", "Bid history"),
        ("purchases", "Your purchases"),
        ("sales", "Your sales"),
        ("pricecheck", "Price check"),
        ("report", "Market report"),
        ("msg", "Chat with a persona"),
        ("qmsg", "Read queued messages"),
        ("sub", "Subscribe to persona updates"),
        ("unsub", "Unsubscribe from persona"),
        ("subs", "List subscriptions"),
        ("announces", "Check pending announcements"),
        ("games", "Play mini-games (lootpower)"),
        ("help", "This help"),
    ]
    for cmd, desc in all_cmds:
        minescript.echo(f" §7• §e\\ah {cmd:12s} §7{desc}")
    minescript.echo("§6═══════════════════════════════")


def cmd_test(args, client):
    """\ah test — Run full system test (read-only, no permanent changes)."""
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "filter_type": "test"}
    minescript.echo("§e[AH] Running system test (read-only)...")
    resp = client.send_and_wait("ah_test", data, "ah_test_response")
    if resp:
        results = resp.get("data", {})
        chat_lines = results.get("chat_lines", [])
        if chat_lines:
            for line in chat_lines:
                minescript.echo(line)
        else:
            overall = results.get("overall", "FAIL")
            color = "§a" if overall == "PASS" else "§e"
            minescript.echo(f"{color}═══ System Test: {overall} {color}═══")
            minescript.echo(f" §7DB: {results.get('database', {}).get('status', '?')}")
            minescript.echo(f" §7Bridge: {results.get('bridge', {}).get('status', '?')}")
            minescript.echo(f" §7RCON: {results.get('rcon', {}).get('status', '?')}")
    else:
        minescript.echo("§c[AH] Test request timed out. Is the server running?")
        minescript.echo("§c[AH] ═══ FAIL ═══")


def cmd_msg(args, client):
    """\\ah msg <persona_id> <message> — Send a message to a persona."""
    player = minescript.player_name()
    if len(args) < 2:
        minescript.echo("§cUsage: \\ah msg <persona_id> <message>")
        return
    persona_id = args[0]
    message = " ".join(args[1:])
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "args": [persona_id, message]}
    minescript.echo(f"§7[Chat] Sending message to {persona_id}...")
    resp = client.send_and_wait("ah_msg", data, "ah_msg_response", timeout=15.0)
    if resp:
        response = resp.get("response", "")
        if response:
            minescript.echo(response)
    else:
        minescript.echo("§c[Chat] Request timed out.")


def cmd_qmsg(args, client):
    """\\ah qmsg [list|next|clear] — Manage queued messages from personas."""
    player = minescript.player_name()
    subcmd = args[0] if args else "list"
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "args": [subcmd]}
    resp = client.send_and_wait("ah_qmsg", data, "ah_msg_response", timeout=15.0)
    if resp:
        result = resp.get("data", {})
        messages = result.get("messages", []) or result.get("response", [])
        if isinstance(messages, list):
            for line in messages:
                minescript.echo(line)
        else:
            minescript.echo(str(messages))
    else:
        minescript.echo("§c[Chat] Request timed out.")


# ── Announce Commands ────────────────────────────────────────────────

def cmd_sub(args, client):
    """\\ah sub <persona_id> — Subscribe to updates about a persona."""
    player = minescript.player_name()
    if not args:
        minescript.echo("§cUsage: \\ah sub <persona_id>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "args": args}
    minescript.echo(f"§7[Announce] Subscribing to {args[0]}...")
    resp = client.send_and_wait("ah_sub", data, "ah_sub_response", timeout=10.0)
    if resp:
        result = resp.get("data", {})
        messages = result.get("message", []) if isinstance(result.get("message"), list) else [result.get("message", "Done.")]
        for line in messages:
            minescript.echo(line)
    else:
        minescript.echo("§c[Announce] Request timed out.")


def cmd_unsub(args, client):
    """\\ah unsub <persona_id> — Unsubscribe from a persona."""
    player = minescript.player_name()
    if not args:
        minescript.echo("§cUsage: \\ah unsub <persona_id>")
        return
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "args": args}
    minescript.echo(f"§7[Announce] Unsubscribing from {args[0]}...")
    resp = client.send_and_wait("ah_unsub", data, "ah_unsub_response", timeout=10.0)
    if resp:
        result = resp.get("data", {})
        messages = result.get("message", []) if isinstance(result.get("message"), list) else [result.get("message", "Done.")]
        for line in messages:
            minescript.echo(line)
    else:
        minescript.echo("§c[Announce] Request timed out.")


def cmd_subs(args, client):
    """\\ah subs — List your subscriptions."""
    player = minescript.player_name()
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player, "args": []}
    minescript.echo("§7[Announce] Fetching subscriptions...")
    resp = client.send_and_wait("ah_subs", data, "ah_subs_response", timeout=10.0)
    if resp:
        result = resp.get("data", {})
        messages = result.get("message", []) if isinstance(result.get("message"), list) else [result.get("message", "None.")]
        for line in messages:
            minescript.echo(line)
    else:
        minescript.echo("§c[Announce] Request timed out.")


def cmd_announces(args, client):
    """\\ah announces [clear] — Check pending announcements."""
    player = minescript.player_name()
    subcmd = args[0] if args else "list"
    data = {"request_uuid": str(uuid.uuid4()), "player_name": player,
            "args": [subcmd]}
    resp = client.send_and_wait("ah_announces", data, "ah_announces_response", timeout=10.0)
    if resp:
        result = resp.get("data", {})
        messages = result.get("message", []) if isinstance(result.get("message"), list) else [result.get("message", "No announcements.")]
        for line in messages:
            minescript.echo(line)
    else:
        minescript.echo("§c[Announce] Request timed out.")



def cmd_games(args, client):
    """\\ah games <game_name> [subcommand...] — Play mini-games from chat!

    Currently supported games:
      lootpower - LootPower role-playing game (adventure, mine, craft, etc.)

    Usage:
      \\ah games lootpower register        - Set up your account
      \\ah games lootpower adventure       - Go on an adventure!
      \\ah games lootpower mine            - Try mining
      \\ah games lootpower inventory       - Check your loot
      \\ah games lootpower leaderboard     - Top players
      \\ah games lootpower profile         - Your profile
      \\ah games lootpower stats           - Your statistics
      \\ah games lootpower craft <a> <b>   - Combine items
      \\ah games lootpower alias [name]    - Set display name
      \\ah games lootpower status          - Game server status
      \\ah games lootpower help            - LootPower help
    """
    if not args:
        minescript.echo("§6═══ §eMini-Games §6═══")
        minescript.echo(" §7• §e\\ah games lootpower §7- LootPower RPG")
        minescript.echo("§6═══════════════════════════")
        minescript.echo("§7Use §e\\ah games <game> §7to start playing!")
        return

    game = args[0].lower()
    subargs = args[1:]

    if game == "lootpower":
        player = minescript.player_name()
        data = {
            "request_uuid": str(uuid.uuid4()),
            "player_name": player,
            "args": subargs,
        }
        minescript.echo(f"§e[LP] Sending request...")
        resp = client.send_and_wait("ah_games", data, "ah_games_response", timeout=10.0)
        if resp:
            chat_lines = resp.get("chat_lines", [])
            if chat_lines:
                for line in chat_lines:
                    minescript.echo(line)
            else:
                # Single-line response
                msg = resp.get("message", "Done.")
                if isinstance(msg, list):
                    for line in msg:
                        minescript.echo(line)
                else:
                    minescript.echo(msg)
        else:
            minescript.echo("§c[LP] Request timed out. Is the server running?")
    else:
        minescript.echo(f"§cUnknown game: {game}. Try §e\\ah games§f to see available games.")

COMMANDS = {
    "list": cmd_list, "bid": cmd_bid, "buy": cmd_buy,
    "mine": cmd_mine, "cancel": cmd_cancel, "search": cmd_search,
    "details": cmd_details, "history": cmd_history,
    "purchases": cmd_purchases, "sales": cmd_sales,
    "pricecheck": cmd_pricecheck, "report": cmd_report,
    "test": cmd_test,
    "msg": cmd_msg, "qmsg": cmd_qmsg,
    "sub": cmd_sub, "unsub": cmd_unsub,
    "subs": cmd_subs, "announces": cmd_announces,
    "games": cmd_games,
    "help": cmd_help,
}


def main():
    try:
        log("INFO", "AH client started")
        minescript.echo("§a[AH] Auction House loaded. §e\\ah help §afor commands.")
        client = AHClient()
        args = sys.argv[1:] if len(sys.argv) > 1 else []
        if not args:
            cmd_help([], client)
            return
        subcommand = args[0].lower()
        subargs = args[1:]
        handler = COMMANDS.get(subcommand)
        if handler:
            handler(subargs, client)
        else:
            minescript.echo(f"§c[AH] Unknown: {subcommand}. \\ah help for commands.")
        client.close()
    except Exception as e:
        minescript.echo(f"§c[AH] Error: {e}")
        log("FATAL", f"{e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    main()
