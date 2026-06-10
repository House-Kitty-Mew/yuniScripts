"""
Crafting System — recipe validation and craft execution.
Matches original craft.py logic exactly.
"""
from typing import Optional
from lp_database import get_db
import lp_config


class CraftingSystem:
    """Handles crafting recipes and execution."""

    def __init__(self):
        self.db = get_db()

    def craft(self, user_id: str, item_one: int, item_one_rarity: int,
              item_two: int, item_two_rarity: int) -> str:
        """
        Attempt to craft two loot items into a new item.

        Args:
            user_id: Player ID
            item_one: Loot ID of first item
            item_one_rarity: Rarity index of first item (0-8)
            item_two: Loot ID of second item
            item_two_rarity: Rarity index of second item (0-8)

        Returns:
            'rarity_name item_name' on success, error message on failure.
        """
        try:
            item_one = int(item_one)
            item_one_rarity = int(item_one_rarity)
            item_two = int(item_two)
            item_two_rarity = int(item_two_rarity)
        except (ValueError, TypeError):
            return "Items Could not be INT"

        # Convert rarity indices to names
        try:
            item_one_rarity_str = lp_config.RARITY_NAMES[item_one_rarity]
            item_two_rarity_str = lp_config.RARITY_NAMES[item_two_rarity]
        except IndexError:
            return "Turning rarity to name failed"

        # Check player has at least 1 of each
        loot_one = self.db.fetchone(
            "SELECT * FROM users_loot WHERE user_id=? AND loot_id=?",
            (user_id, item_one)
        )
        if loot_one is None:
            return "Item(s) Could not be found"

        rarity_val_one = loot_one[item_one_rarity_str]
        if rarity_val_one <= 0:
            return "Item(s) Could not be found"

        loot_two = self.db.fetchone(
            "SELECT * FROM users_loot WHERE user_id=? AND loot_id=?",
            (user_id, item_two)
        )
        if loot_two is None:
            return "Item(s) Could not be found"

        rarity_val_two = loot_two[item_two_rarity_str]
        if rarity_val_two <= 0:
            return "Item(s) Could not be found"

        # Rarities must match
        if item_one_rarity_str != item_two_rarity_str:
            return "Item raritys do not match"

        # Find recipe (order-insensitive)
        recipe = self.db.fetchone(
            """SELECT * FROM craft_rep
               WHERE (item_one_id=? AND item_two_id=?)
                  OR (item_one_id=? AND item_two_id=?)""",
            (item_one, item_two, item_two, item_one)
        )
        if recipe is None:
            return "Recipe does not exist"

        # Check if user already has a craft entry
        user_craft = self.db.fetchone(
            "SELECT * FROM user_craft WHERE user_id=? AND craft_id=?",
            (user_id, recipe["rep_item_id"])
        )

        if user_craft:
            # Increment
            self.db.execute(
                "UPDATE user_craft SET amount = amount + 1 WHERE user_id=? AND craft_id=?",
                (user_id, recipe["rep_item_id"])
            )
        else:
            # Create new
            self.db.execute(
                "INSERT INTO user_craft (user_id, craft_id, amount, rarity) VALUES (?,?,1,?)",
                (user_id, recipe["rep_item_id"], item_one_rarity_str)
            )

        # Consume the two input items
        self.db.execute(
            f"""UPDATE users_loot SET
                loot_amount = loot_amount - 1,
                {item_one_rarity_str} = {item_one_rarity_str} - 1
            WHERE user_id=? AND loot_id=?""",
            (user_id, item_one)
        )
        self.db.execute(
            f"""UPDATE users_loot SET
                loot_amount = loot_amount - 1,
                {item_two_rarity_str} = {item_two_rarity_str} - 1
            WHERE user_id=? AND loot_id=?""",
            (user_id, item_two)
        )
        self.db.commit()

        return f"{item_one_rarity_str} {recipe['rep_item']}"

    def get_recipes(self) -> list:
        """Get all crafting recipes."""
        rows = self.db.fetchall("SELECT * FROM craft_rep")
        return [dict(r) for r in rows]

    def get_user_crafts(self, user_id: str) -> list:
        """Get player's crafted items."""
        rows = self.db.fetchall(
            "SELECT * FROM user_craft WHERE user_id=?", (user_id,)
        )
        return [dict(r) for r in rows]