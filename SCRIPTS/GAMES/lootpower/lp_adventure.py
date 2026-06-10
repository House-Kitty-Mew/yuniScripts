"""
Adventure System — coordinates adventure turns and loot drops.
"""
from typing import List
from lp_player import PlayerService
from lp_chance import LootChanceEngine


class AdventureSystem:
    """Handles adventure execution for active players."""

    def __init__(self, player_svc: PlayerService = None, chance_engine: LootChanceEngine = None):
        self.player_svc = player_svc or PlayerService()
        self.chance_engine = chance_engine or LootChanceEngine()

    def adventure(self, user_id: str, user_name: str = "",
                  show_story: str = "1") -> str:
        """
        Execute an adventure for the player.

        Args:
            user_id: Player ID
            user_name: Display name (for future phooks)
            show_story: "1" for full story, "0" for compact

        Returns:
            Message string to display to the player.
        """
        turns = self.player_svc.validate_turn(user_id)
        if turns < 1:
            return (
                "You are too tired to adventure right now.\n"
                "Rest for a minute."
            )

        # Find loot (first attempt)
        found_loot = self.chance_engine.find_loot(user_id, show_story)

        # Original behavior: retry once if empty
        if found_loot[1] == "You went adventuring but no loot could be found":
            found_loot = self.chance_engine.find_loot(user_id, show_story)

        return found_loot[1] + " \n Energy: " + str(turns)