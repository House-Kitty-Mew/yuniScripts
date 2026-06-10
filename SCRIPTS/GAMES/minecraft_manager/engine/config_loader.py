"""Mock config_loader for testing."""


def get_config_path(name: str) -> str:
    return f"/tmp/test_config_{name}.json"


def load_config(name_or_path: str) -> dict:
    return {}


def save_config(name_or_path: str, config: dict) -> bool:
    return True
