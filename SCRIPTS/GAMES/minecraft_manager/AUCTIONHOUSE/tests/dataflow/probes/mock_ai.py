"""
mock_ai.py — Mock DeepSeek AI for simulation cycle testing.

Provides a configurable mock that returns realistic AI responses
for testing the simulation cycle without requiring actual API calls.

    Usage:
        mock_ai = MockDeepSeekAI()

        mock_ai.set_response("price_adjust", {...})
        
        # In test:
            with mock_ai.patch():

                result = run_simulation_cycle()

"""

import json
import threading
from typing import Any, Optional
from unittest.mock import patch


class MockDeepSeekAI:
    """Configurable mock for the DeepSeek API integration.

    Provides preset response templates for different simulation scenarios.
    """

    def __init__(self, trace=None):
        self._responses: dict[str, Any] = {}
        self._call_count = 0
        self._call_log: list[dict] = []
        self._lock = threading.Lock()
        self._default_response = self._make_default_response()
        self._trace_ref = trace

    # ── Response templates ───────────────────────────────────────

    @staticmethod
    def _make_default_response() -> dict:
        """Return a minimal valid AI response."""
        return {
            "market_assessment": "Market is stable with normal trading activity.",
            "price_adjustments": [],
            "stock_adjustments": [],
            "events": [],
            "rare_items_to_list": [],
            "stale_recommendations": [],
            "notes": [
                {
                    "category": "market_health",
                    "content": "Market assessment: stable conditions."
                }
            ],
            "announcement": None,
        }

    @staticmethod
    def make_price_adjust_response(item_id: str, new_price: float,
                                   reason: str = "test") -> dict:
        """Return a response with a single price adjustment."""

        resp = MockDeepSeekAI._make_default_response()
        resp["price_adjustments"] = [
            {"item_id": item_id, "new_base_price": new_price, "reason": reason}
        ]
        return resp

    @staticmethod
    def make_event_response(event_name: str, event_title: str,
                            event_type: str = "seasonal",
                            rarity_tier: str = "small") -> dict:
        """Return a response with a market event."""

        resp = MockDeepSeekAI._make_default_response()
        resp["events"] = [{
            "action": "start",
            "event_name": event_name,
            "event_title": event_title,
            "event_type": event_type,
            "rarity_tier": rarity_tier,
            "affected_items": ["minecraft:coal"],
            "price_multiplier": 1.5,
            "demand_boost": 1.2,
        }]
        return resp

    @staticmethod
    def make_rare_item_response(item_id: str, price: float,
                                rarity_tier: str = "Uncommon") -> dict:
        """Return a response with a rare item listing."""

        resp = MockDeepSeekAI._make_default_response()
        resp["rare_items_to_list"] = [{
            "item_id": item_id,
            "count": 1,
            "price": price,
            "enchantments": ["minecraft:sharpness:3"],
            "lore": ["A finely crafted blade"],
            "rarity_tier": rarity_tier,
            "durability": 100,
        }]
        return resp

    @staticmethod
    def make_stale_adjust_response(listing_uuid: str,
                                   recommendation: str = "lower_price",
                                   suggested_price: float = 5.0) -> dict:
        """Return a response with a stale listing recommendation."""

        resp = MockDeepSeekAI._make_default_response()
        resp["stale_recommendations"] = [{
            "listing_uuid": listing_uuid,
            "recommendation": recommendation,
            "suggested_price": suggested_price,
        }]
        return resp

    @staticmethod
    def make_invalid_json_response() -> str:
        """Return a malformed JSON response for error path testing."""
        return "This is not valid JSON at all!!!"

    @staticmethod
    def make_partial_response() -> str:
        """Return a JSON response missing required fields."""
        return json.dumps({
            "market_assessment": "Partial response - no actions.",
            # Missing: price_adjustments, stock_adjustments, etc.
        })

    # ── Configuration ────────────────────────────────────────────

    def set_response(self, response_type: str, data: Any = None):
        """Set a custom response for a specific scenario.

        Args:
            response_type: Key to identify this response
            data: The response data (dict or raw JSON string)
        """
        with self._lock:
            self._responses[response_type] = data

    def set_sequential_responses(self, responses: list[Any]):
        """Set responses that will be returned in sequence on each call."""
        with self._lock:
            self._responses["__sequential__"] = list(responses)
            self._seq_index = 0

    # ── Mock call handler ────────────────────────────────────────

    def call_deepseek(self, system_prompt: str, user_prompt: str,
                      **kwargs) -> str:
        """Mock the DeepSeek API call.

        Returns pre-configured response or default.
        """
        with self._lock:
            self._call_count += 1

            call_record = {
                "call_number": self._call_count,
                "system_prompt_len": len(system_prompt),
                "user_prompt_len": len(user_prompt),
                "kwargs": kwargs,
            }

            # Check for sequential responses first
            if "__sequential__" in self._responses:
                seq = self._responses["__sequential__"]
                idx = getattr(self, '_seq_index', 0)
                if idx < len(seq):
                    response = seq[idx]
                    self._seq_index = idx + 1
                else:
                    response = self._default_response
            elif self._responses:
                # Return the most recently set custom response
                last_key = list(self._responses.keys())[-1]
                if last_key != "__sequential__":
                    response = self._responses[last_key]
                else:
                    response = self._default_response
            else:
                response = self._default_response

            # Convert to JSON string if dict
            if isinstance(response, dict):
                response = json.dumps(response)

            call_record["response_preview"] = str(response)[:200]
            self._call_log.append(call_record)
            if self._trace_ref:
                self._trace_ref.record(
                    "ai.api_call",
                    "mocked",
                    call_number=self._call_count,
                    response_len=len(response),
                )
            return response

    def reset(self):
        """Reset all state."""
        with self._lock:
            self._responses.clear()
            self._call_count = 0
            self._call_log.clear()

    @property
    def call_count(self) -> int:
        return self._call_count

    def get_call_log(self) -> list[dict]:
        with self._lock:
            return list(self._call_log)

    # ── Patching ─────────────────────────────────────────────────

    def patch(self):
        """Return a context manager that patches the AI API call.

        Usage:
            with mock_ai.patch():
                run_simulation_cycle()

        """
        return patch(
            "AUCTIONHOUSE.ah_ai_engine._call_deepseek",
            side_effect=self.call_deepseek
        )
