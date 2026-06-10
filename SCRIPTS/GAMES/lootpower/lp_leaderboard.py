from typing import Optional
"""
Leaderboard — ranking players by lootpower + chance boost.
"""
from typing import List
from lp_database import get_db


class Leaderboard:
    """Generates player rankings."""

    def __init__(self):
        self.db = get_db()

    def get_rankings(self, limit: int = 50) -> List[dict]:
        """
        Calculate player rankings.

        Uses original formula:
            total_ranking = (global_chance_boost * total_items) + lootpower

        Returns sorted list of dicts with rank, name, score.
        """
        import random

        users = self.db.fetchall(
            "SELECT user_id, user_name, global_loot_chance_boost FROM users WHERE is_active=1"
        )

        rankings = []
        for user in users:
            # Count total loot items
            loot_row = self.db.fetchone(
                "SELECT COALESCE(SUM(loot_amount), 0) AS total FROM users_loot WHERE user_id=?",
                (user["user_id"],)
            )
            loot_count = loot_row["total"] if loot_row else 0

            # Get lootpower
            lp_row = self.db.fetchone(
                "SELECT lootpower FROM lootpower_table WHERE user_ID=?",
                (user["user_id"],)
            )
            lootpower = float(lp_row["lootpower"]) if lp_row else 0.0

            # Original formula
            uq_sim = random.randint(1, 10)  # (original had this for uniqueness)
            total_ranking = (
                float(user["global_loot_chance_boost"]) * float(loot_count)
            ) + lootpower
            total_ranking = round(total_ranking, 2)

            rankings.append({
                "name": user["user_name"],
                "score": total_ranking,
                "lootpower": lootpower,
                "item_count": loot_count,
            })

        # Sort descending by score
        rankings.sort(key=lambda r: r["score"], reverse=True)

        # Assign ranks
        for i, r in enumerate(rankings):
            r["rank"] = i + 1

        return rankings[:limit]

    def get_player_rank(self, user_id: str) -> Optional[dict]:
        """Get a specific player's rank."""
        rankings = self.get_rankings(limit=9999)
        for r in rankings:
            uid_row = self.db.fetchone(
                "SELECT user_id FROM users WHERE user_name=?", (r["name"],)
            )
            if uid_row and uid_row["user_id"] == user_id:
                return r
        return None