"""Revert test files to proper pytest style."""

import glob

# First, rewrite conftest.py to original pytest form
conftest_content = '''"""
conftest.py — Test fixtures for SIMULATED_TRADE tests.

Provides:
  - In-memory database for testing
  - Mock state registry
  - Sample personas, items, routes
  - Cleanup between tests
"""

import json, os, sys, uuid, pytest
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

# Ensure auction house modules are importable
AH_DIR = Path(__file__).parent.parent.parent.parent.parent  # AUCTIONHOUSE/
sys.path.insert(0, str(AH_DIR.parent.parent.parent))  # dev-yuniScripts/

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import load_config


SAMPLE_PERSONAS = {
    "alice-uuid": {
        "name": "Alice",
        "archetype": "merchant",
        "job": "trader",
        "region": "overworld",
        "personality_traits": json.dumps({
            "trade_willingness": 0.8,
            "need_urgency_factor": 0.4,
            "criminal_tendency": 0.0,
            "generosity": 0.6,
            "risk_tolerance": 0.2,
        }),
    },
    "bob-uuid": {
        "name": "Bob",
        "archetype": "miner",
        "job": "miner",
        "region": "overworld",
        "personality_traits": json.dumps({
            "trade_willingness": 0.5,
            "need_urgency_factor": 0.6,
            "criminal_tendency": 0.1,
            "generosity": 0.3,
            "risk_tolerance": 0.4,
        }),
    },
    "charlie-uuid": {
        "name": "Charlie",
        "archetype": "bandit",
        "job": "raider",
        "region": "nether",
        "personality_traits": json.dumps({
            "trade_willingness": 0.2,
            "need_urgency_factor": 0.1,
            "criminal_tendency": 0.9,
            "generosity": 0.1,
            "risk_tolerance": 0.9,
        }),
    },
}

SAMPLE_CLAIMS = {
    "alice-uuid": "claim-alice-1",
    "bob-uuid": "claim-bob-1",
    "charlie-uuid": "claim-charlie-1",
}

SAMPLE_LOCATIONS = {
    "alice-uuid": {"x": 0, "z": 0, "area": "spawn"},
    "bob-uuid": {"x": 100, "z": 100, "area": "plains"},
    "charlie-uuid": {"x": 500, "z": 500, "area": "nether_fortress"},
}


@pytest.fixture(autouse=True)
def setup_state_registry():
    """Ensure state registry is available and populated with test data."""
    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state

    clear_state()
    state = get_state()

    state.set("active_personas", [
        {"uuid": "alice-uuid", "name": "Alice"},
        {"uuid": "bob-uuid", "name": "Bob"},
        {"uuid": "charlie-uuid", "name": "Charlie"},
    ], "SIMULATED_TRADE")

    state.set("persona_locations", dict(SAMPLE_LOCATIONS), "SIMULATED_TRADE")
    state.set("persona_claims", dict(SAMPLE_CLAIMS), "SIMULATED_TRADE")
    state.set("persona_profiles", dict(SAMPLE_PERSONAS), "SIMULATED_TRADE")

    state.set("persona_inventories", {
        "alice-uuid": {"minecraft:diamond": 10, "minecraft:emerald": 50, "minecraft:wheat": 64},
        "bob-uuid": {"minecraft:iron_ingot": 32, "minecraft:stone": 128, "minecraft:wood": 64},
        "charlie-uuid": {"minecraft:diamond_sword": 1, "minecraft:netherite_ingot": 2},
    }, "SIMULATED_TRADE")

    state.set("persona_finances", {
        "alice-uuid": {"balance": 5000.0, "lifetime_income": 10000.0, "lifetime_spending": 5000.0},
        "bob-uuid": {"balance": 2000.0, "lifetime_income": 5000.0, "lifetime_spending": 3000.0},
        "charlie-uuid": {"balance": 500.0, "lifetime_income": 1000.0, "lifetime_spending": 500.0},
    }, "SIMULATED_TRADE")

    state.set("persona_needs", {
        "alice-uuid": {"minecraft:iron_ingot": {"urgency": 7, "quantity": 16, "quantity_obtained": 0}},
        "bob-uuid": {"minecraft:diamond": {"urgency": 8, "quantity": 5, "quantity_obtained": 0}},
        "charlie-uuid": {},
    }, "SIMULATED_TRADE")

    state.set("persona_relationships", {
        "alice-uuid": {"bob-uuid": 30.0, "charlie-uuid": -10.0},
        "bob-uuid": {"alice-uuid": 30.0, "charlie-uuid": -20.0},
        "charlie-uuid": {"alice-uuid": -10.0, "bob-uuid": -20.0},
    }, "SIMULATED_TRADE")

    state.set("persona_skills", {
        "alice-uuid": {"barter": 60.0, "combat": 10.0, "stealth": 20.0},
        "bob-uuid": {"barter": 30.0, "combat": 40.0, "stealth": 15.0},
        "charlie-uuid": {"barter": 10.0, "combat": 70.0, "stealth": 50.0},
    }, "SIMULATED_TRADE")

    yield

    clear_state()


@pytest.fixture(autouse=True)
def setup_database():
    """Set up in-memory SQLite database for testing."""
    from AUCTIONHOUSE.ah_database import get_db as ah_get_db
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import init_database
    init_database()

    yield

    db = ah_get_db()
    tables = [
        "ext_tr_trades", "ext_tr_routes", "ext_tr_pending_trades",
        "ext_tr_banditry", "ext_tr_reputation", "ext_tr_caravans",
        "ext_tr_world_events", "ext_tr_trade_cooldowns",
        "ext_tr_route_trade_log", "ext_tr_resource_state",
    ]
    for table in tables:
        try:
            db.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    db.commit()


@pytest.fixture
def sample_persona_ids() -> dict:
    """Return sample persona UUID mapping."""
    return {"alice": "alice-uuid", "bob": "bob-uuid", "charlie": "charlie-uuid"}


@pytest.fixture
def sample_route(setup_database):
    """Create a sample trade route for testing."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import construct_route
    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
    state = get_state()
    state.set("all_claims", {
        "claim-alice-1": {"owner": "alice-uuid", "name": "Alice's Base"},
        "claim-bob-1": {"owner": "bob-uuid", "name": "Bob's Mine"},
    }, "TRADE_TEST")

    state.set("persona_inventories", {
        "alice-uuid": {"minecraft:wood": 256, "minecraft:stone": 128,
                       "minecraft:diamond": 10, "minecraft:emerald": 50},
        "bob-uuid": {"minecraft:iron_ingot": 32, "minecraft:stone": 128},
        "charlie-uuid": {"minecraft:diamond_sword": 1},
    }, "SIMULATED_TRADE")

    state.set("persona_finances", {
        "alice-uuid": {"balance": 5000.0},
        "bob-uuid": {"balance": 2000.0},
        "charlie-uuid": {"balance": 500.0},
    }, "SIMULATED_TRADE")

    result = construct_route(
        persona_uuid="alice-uuid",
        from_claim_uuid="claim-alice-1",
        to_claim_uuid="claim-bob-1",
        road_segments=[{"x": 0, "z": 0}, {"x": 50, "z": 50}, {"x": 100, "z": 100}],
    )
    return result.get("route_uuid") if result.get("ok") else None
'''

with open('tests/conftest.py', 'w') as f:
    f.write(conftest_content)

print("Reverted conftest.py to pytest style")

# Now revert all test files to original pytest style  
for fpath in glob.glob('tests/test_*.py'):
    with open(fpath) as f:
        content = f.read()
    
    # Remove any unittest imports we added
    content = content.replace('import unittest\n', '')
    content = content.replace('import unittest ', '')
    
    # Revert class declarations
    import re
    content = re.sub(
        r'class (Test\w+)\(unittest\.TestCase\):',
        r'class \1:',
        content
    )
    
    # Add import pytest back
    content = content.replace('import json, re', 'import json, re, pytest')
    content = content.replace('import json', 'import json, pytest', 1) if 'import json' in content else 'import pytest\n' + content
    
    # Remove any duplicate empty lines from import section
    content = re.sub(r'\n\n\n', '\n\n', content)
    
    with open(fpath, 'w') as f:
        f.write(content)
    
    print(f"Reverted {fpath}")

print("\nAll files reverted to pytest style")
