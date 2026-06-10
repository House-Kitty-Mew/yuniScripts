# Config Updater API

## Purpose
Validates INI configuration files and applies changes via a plugin-like
validator system. This is a **reference implementation** – customize the
`VALIDATORS` dictionary in `main.py` for your own config keys.

## Commands
None (runs once and exits; restart_policy=never in meta.info).

## Hooks
None provided.

## Flow
1. Loads `DATA/config_updater.ini` (from centralized `./DATA/`; legacy `DATA/main.ini` auto-migrated)
2. Runs all registered validators against each section/key
3. Prints validated values or validation errors
4. Calls `apply_config()` (override this function with your logic)

## Extending
Add validators in `main.py`:
```python
register_validator("section", "key", my_validator_func)
```
The function signature: `(raw_value: str) -> (bool, normalized_value_or_error)`
