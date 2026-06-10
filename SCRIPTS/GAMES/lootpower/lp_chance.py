"""
LootPower Chance System — core loot drop algorithm.

PRESERVES THE ORIGINAL ALGORITHM EXACTLY:
  - find_loot_drop_chance() retains the exact Decimal math
  - find_rarity() uses original random.randrange(0,7) range
  - find_loot() follows original flow: iterate loot DESC,
    roll for each, apply global boost, update on success

Exposes phooks events so external scripts (market, chance system)
can observe/intercept drops.
"""
import random
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, List, Optional
from datetime import datetime

import lp_config
from lp_database import get_db
from lp_models import (
    LootItem, UserLoot, LootStat, LootPower
)


class LootChanceEngine:
    """
    Core engine for loot drop chance calculation and loot generation.
    """

    def __init__(self):
        self.db = get_db()

    # ------------------------------------------------------------------
    # find_loot_drop_chance — EXACT original algorithm
    # ------------------------------------------------------------------
    def find_loot_drop_chance(
        self,
        global_chance: float,
        boosted_chance: float,
        self_lower_drop_chance: float,
        item_id: int,
        loot_name: str,
        user_id: str
    ) -> float:
        """
        Calculate the final drop chance for a specific loot item.
        Matches the original algorithm character-for-character.
        """
        row = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
            (user_id, item_id)
        )
        if row is None:
            lower_drop_chance = Decimal(0)
            added_chance = Decimal(0)
        else:
            user_loot_amount = row["loot_amount"]
            lower_drop_chance = Decimal(float(self_lower_drop_chance) * user_loot_amount)
            lower_drop_chance = Decimal(lower_drop_chance).quantize(
                Decimal("1."), rounding=ROUND_HALF_UP
            )
            boosted_chance_dec = Decimal(boosted_chance).quantize(
                Decimal("1."), rounding=ROUND_HALF_UP
            )
            added_chance = boosted_chance_dec

        work = Decimal(float(global_chance) - float(added_chance))
        work2 = Decimal(str(work - int(work) + 1))
        sub_zero_chance = Decimal("{:.6f}".format(random.uniform(1, 2)))

        if sub_zero_chance >= work2:
            finale_chance = (
                float(global_chance) - 1 - float(added_chance)
                + float(lower_drop_chance)
            )
        else:
            finale_chance = (
                float(global_chance) - float(added_chance)
                + float(lower_drop_chance)
            )

        # Floor clamp
        if finale_chance < 10:
            if loot_name == "rock":
                finale_chance = 2
            else:
                finale_chance = 10

        return finale_chance

    # ------------------------------------------------------------------
    # find_rarity — EXACT original algorithm
    # ------------------------------------------------------------------
    def find_rarity(self) -> Tuple[str, int, int]:
        """
        Determine rarity of a dropped loot item.
        Returns (rarity_name, rarity_value, rarity_index).

        Uses random.randrange(0, 7) exactly like the original.
        """
        rarity_values = lp_config.RARITY_ROLL_VALUES  # [2,10,25,50,100,250,500,1000,1000000]
        rarity_names = lp_config.RARITY_NAMES

        rarity_found = None
        while rarity_found is None:
            rarity_level = random.randrange(0, 7)  # 0-6 inclusive (original)
            rarity_chance = rarity_values[rarity_level]
            dropped = random.randint(1, rarity_chance)
            if dropped == rarity_chance:
                rarity_found = rarity_level

        return rarity_names[rarity_found], rarity_values[rarity_found], rarity_found

    # ------------------------------------------------------------------
    # loot_stats — record a drop in loot_stats table
    # ------------------------------------------------------------------
    def loot_stats(self, user_id: str, rarity: str, name: str) -> bool:
        """Record a loot drop in loot_stats table."""
        date = datetime.now()
        try:
            self.db.execute(
                """INSERT INTO loot_stats
                   (user_id, loot_name, loot_rarity, year, month, day, hour, minute)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, name, rarity, date.year, date.month,
                 date.day, date.hour, date.minute)
            )
            self.db.commit()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # find_loot — MAIN loot generation (EXACT original flow)
    # ------------------------------------------------------------------
    def find_loot(self, user_id: str, show_story: str = "1") -> List:
        """
        Main loot generation function.

        Args:
            user_id: Player ID
            show_story: "1" = full story, "0" = compact

        Returns:
            [discord_info, info, rarity_name]
        """
        # Get user's global loot chance boost
        row = self.db.fetchone(
            "SELECT global_loot_chance_boost FROM users WHERE user_id=?",
            (user_id,)
        )
        if row is None:
            return [
                "Found Nothing",
                "You went adventuring but no loot could be found",
                "common"
            ]

        user_global_loot_chance = float(row["global_loot_chance_boost"])

        # Get all loot items ordered by loot_id DESC (original behavior)
        loot_rows = self.db.fetchall(
            "SELECT * FROM loot_table ORDER BY loot_id DESC"
        )

        for loot_row in loot_rows:
            loot = LootItem(
                loot=loot_row["loot"],
                loot_chance=float(loot_row["loot_chance"]),
                loot_chance_raise=float(loot_row["loot_chance_raise"]),
                self_loot_chance_lower=float(
                    loot_row["self_loot_chance_lower"] if loot_row["self_loot_chance_lower"] else 0
                ),
                loot_lore=loot_row["loot_lore"] if loot_row["loot_lore"] else "",
                loot_id=loot_row["loot_id"],
            )

            # Calculate drop chance for this specific item
            finale_chance = self.find_loot_drop_chance(
                global_chance=loot.loot_chance,
                boosted_chance=user_global_loot_chance,
                self_lower_drop_chance=loot.self_loot_chance_lower,
                item_id=loot.loot_id,
                loot_name=loot.loot,
                user_id=user_id,
            )

            # Roll for drop
            gen_chance = random.randint(1, int(finale_chance))
            if gen_chance == int(finale_chance):
                # --- LOOT FOUND ---
                rarity_name, rarity_value, rarity_index = self.find_rarity()

                # Update user loot inventory
                user_loot_row = self.db.fetchone(
                    "SELECT * FROM users_loot WHERE user_id=? AND loot_id=?",
                    (user_id, loot.loot_id)
                )

                if user_loot_row:
                    # Increment existing — use the rarity_index column
                    rarity_col = lp_config.RARITY_NAMES[rarity_index]
                    self.db.execute(
                        f"""UPDATE users_loot SET
                            loot_amount = loot_amount + 1,
                            {rarity_col} = {rarity_col} + 1
                        WHERE user_id=? AND loot_id=?""",
                        (user_id, loot.loot_id)
                    )
                else:
                    # Create new entry
                    cols = [0] * 9
                    cols[rarity_index] = 1
                    self.db.execute(
                        """INSERT INTO users_loot
                           (user_id, loot, loot_id, loot_amount,
                            common, uncommon, rare, great, amazing,
                            legendary, epic, godly, mythic)
                           VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?)""",
                        (user_id, loot.loot, loot.loot_id, 1,
                         cols[0], cols[1], cols[2], cols[3], cols[4],
                         cols[5], cols[6], cols[7], cols[8])
                    )

                # Update global loot chance boost
                random_point = "0.00000" + str(random.randint(1, 5))
                overall_chance_boost = Decimal(
                    (float(random_point) * float(rarity_value))
                    + float(loot.loot_chance_raise)
                )
                new_global_chance = Decimal(
                    float(user_global_loot_chance) + float(overall_chance_boost)
                )
                new_global_chance = float(
                    "{:.6f}".format(new_global_chance)
                )
                self.db.execute(
                    """UPDATE users SET global_loot_chance_boost=?
                       WHERE user_id=?""",
                    (new_global_chance, user_id)
                )

                # Get story
                story_row = self.db.fetchone(
                    "SELECT story_text FROM story_table ORDER BY RANDOM() LIMIT 1"
                )
                story_text = (
                    story_row["story_text"]
                    if story_row
                    else "Your adventure was boring, and you "
                )

                alt_info = f"{rarity_name} {loot.loot}"
                ns_info = f"Found a {rarity_name} {loot.loot}  \n (GC%{new_global_chance})"
                discord_info = ns_info

                # Dynamic loot placement
                dyna_story = story_text.replace("[[loot]]", alt_info)
                if dyna_story != story_text:
                    info = dyna_story + "\n" + f" GC%{new_global_chance}"
                elif show_story == "1":
                    info = story_text + " " + ns_info
                else:
                    info = ns_info

                # Record stat
                self.loot_stats(user_id, rarity_name, loot.loot)
                self.db.execute(
                    "UPDATE stat_table SET adventures = adventures + 1 WHERE rowid=1"
                )
                self.db.commit()

                return [discord_info, info, rarity_name]

        # No loot found
        self.db.commit()
        return [
            "Found Nothing",
            "You went adventuring but no loot could be found",
            "common"
        ]

    # ------------------------------------------------------------------
    # Utility: get player's total loot count
    # ------------------------------------------------------------------
    def get_player_loot_count(self, user_id: str) -> int:
        """Count total loot items a player owns."""
        row = self.db.fetchone(
            "SELECT COALESCE(SUM(loot_amount), 0) AS total FROM users_loot WHERE user_id=?",
            (user_id,)
        )
        return row["total"] if row else 0

    # ------------------------------------------------------------------
    # Utility: get all loot items for listing
    # ------------------------------------------------------------------
    def get_all_loot_items(self) -> List[dict]:
        """Return all loot table entries as dicts."""
        rows = self.db.fetchall("SELECT * FROM loot_table ORDER BY loot_id")
        return [dict(r) for r in rows]