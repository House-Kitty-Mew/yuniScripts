"""
Auction House — listing and bidding on loot items.
"""
from typing import Optional, List
from lp_database import get_db


class AuctionHouse:
    """Handles auction listings, bids, and completions."""

    def __init__(self):
        self.db = get_db()

    def create_listing(self, seller_id: str, loot_id: int,
                       loot_amount: int = 1,
                       payment_type: int = 0, payment_id: int = 0,
                       payment_amount: int = 0) -> Optional[int]:
        """Create a new auction listing. Returns auction_id or None."""
        # Verify seller has enough loot
        row = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
            (seller_id, loot_id)
        )
        if not row or row["loot_amount"] < loot_amount:
            return None

        # Deduct loot from seller
        self.db.execute(
            "UPDATE users_loot SET loot_amount = loot_amount - ? WHERE user_id=? AND loot_id=?",
            (loot_amount, seller_id, loot_id)
        )

        # Create listing
        self.db.execute(
            """INSERT INTO auction
               (seller_id, loot_id, loot_amount, payment_type, payment_id, payment_amount)
               VALUES (?,?,?,?,?,?)""",
            (seller_id, loot_id, loot_amount, payment_type, payment_id, payment_amount)
        )
        self.db.commit()

        # Get the auction_id
        row = self.db.fetchone("SELECT last_insert_rowid() as id")
        return row["id"] if row else None

    def get_active_listings(self) -> List[dict]:
        """Get all active auction listings."""
        rows = self.db.fetchall(
            "SELECT * FROM auction ORDER BY auction_id DESC"
        )
        return [dict(r) for r in rows]

    def buy_listing(self, auction_id: int, buyer_id: str) -> str:
        """Buy a listing outright. Returns result message."""
        row = self.db.fetchone(
            "SELECT * FROM auction WHERE auction_id=?", (auction_id,)
        )
        if not row:
            return "Listing not found"
        if row["seller_id"] == buyer_id:
            return "Cannot buy your own listing"

        # Transfer payment amount (simplified — real system uses payment_type/ID)
        # For now, payment is abstract — the advanced market system hooks handle it
        self.db.execute(
            "DELETE FROM auction WHERE auction_id=?", (auction_id,)
        )
        # Give loot to buyer
        loot_row = self.db.fetchone(
            "SELECT * FROM users_loot WHERE user_id=? AND loot_id=?",
            (buyer_id, row["loot_id"])
        )
        if loot_row:
            self.db.execute(
                "UPDATE users_loot SET loot_amount = loot_amount + ? WHERE user_id=? AND loot_id=?",
                (row["loot_amount"], buyer_id, row["loot_id"])
            )
        else:
            # Create entry with just amount (no rarity info)
            self.db.execute(
                """INSERT INTO users_loot
                   (user_id, loot, loot_id, loot_amount)
                   VALUES (?, (SELECT loot FROM loot_table WHERE loot_id=?), ?, ?)""",
                (buyer_id, row["loot_id"], row["loot_id"], row["loot_amount"])
            )
        self.db.commit()
        return f"Purchased listing {auction_id}"

    def cancel_listing(self, auction_id: int, user_id: str) -> str:
        """Cancel a listing and return loot to seller."""
        row = self.db.fetchone(
            "SELECT * FROM auction WHERE auction_id=?", (auction_id,)
        )
        if not row:
            return "Listing not found"
        if row["seller_id"] != user_id:
            return "Not your listing"

        # Return loot to seller
        self.db.execute(
            "UPDATE users_loot SET loot_amount = loot_amount + ? WHERE user_id=? AND loot_id=?",
            (row["loot_amount"], user_id, row["loot_id"])
        )
        self.db.execute(
            "DELETE FROM auction WHERE auction_id=?", (auction_id,)
        )
        self.db.commit()
        return f"Listing {auction_id} cancelled"