"""
first-run-setup — Interactive YuniScripts configuration wizard.

Detects a fresh install (via engine_config's fresh_install flag) and
walks the server owner through setting up all mandatory config values
interactively.  Uses sensible defaults for optional settings.

Flow:
  1. Check if fresh install (or --force flag)
  2. Display welcome banner
  3. Prompt for MANDATORY settings (block if empty):
     - DeepSeek API Key (needed for AI economy)
     - RCON Password (needed for Minecraft server control)
  4. Prompt for OPTIONAL settings (defaults provided):
     - Steam API Key, Nextcloud URL, signing keys, etc.
  5. Write all config files to DATA/
  6. Clear fresh_install flag
  7. Print summary

Usage:
    python3 SCRIPTS/TOOLS/first-run-setup/main.py
    python3 SCRIPTS/TOOLS/first-run-setup/main.py --force  # Re-run even if configured
"""

import sys, os, json, signal
from pathlib import Path
from getpass import getpass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.config_loader import (
    load_config, save_config, get_data_dir,
    is_fresh_install, mark_setup_complete, get_config_path,
)


# ── Constants ───────────────────────────────────────────────────────

BANNER = r"""
╔══════════════════════════════════════════════════════════════╗
║              YuniScripts — First-Run Setup                  ║
║                                                              ║
║  This wizard will help you configure your YuniScripts        ║
║  engine.  Mandatory fields are marked with *.  You can       ║
║  change any setting later by editing files in DATA/.         ║
╚══════════════════════════════════════════════════════════════╝
"""

_COLOR_RED = "\033[91m"
_COLOR_GREEN = "\033[92m"
_COLOR_YELLOW = "\033[93m"
_COLOR_CYAN = "\033[96m"
_COLOR_RESET = "\033[0m"


def color(text, code):
    if sys.stdout.isatty():
        return f"{code}{text}{_COLOR_RESET}"
    return text


def red(text):   return color(text, _COLOR_RED)
def green(text): return color(text, _COLOR_GREEN)
def yellow(text):return color(text, _COLOR_YELLOW)
def cyan(text):  return color(text, _COLOR_CYAN)


# ── Prompt helpers ─────────────────────────────────────────────────

def prompt_mandatory(prompt_text: str, default: str = "",
                     secret: bool = False) -> str:
    """Prompt the user for a mandatory value.  Keeps asking until
    a non-empty answer is given, unless a default is provided."""
    while True:
        if default:
            display = f"{prompt_text} [{green(default)}]: "
        else:
            display = f"{yellow(prompt_text)}* [{red('REQUIRED')}]: "

        try:
            if secret:
                value = getpass(display)
            else:
                value = input(display).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{yellow('Setup cancelled.')}")
            sys.exit(1)

        if value:
            return value
        if default:
            return default
        print(red(f"  ⚠ This value is required. Please enter it."))


def prompt_optional(prompt_text: str, default: str = "") -> str:
    """Prompt the user for an optional value."""
    display = f"{prompt_text} [{green(default or 'none')}]: "
    try:
        value = input(display).strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{yellow('Setup cancelled.')}")
        sys.exit(1)
    return value if value else default


def prompt_boolean(prompt_text: str, default: bool = True) -> bool:
    """Prompt for a yes/no answer."""
    d = "Y/n" if default else "y/N"
    display = f"{prompt_text} [{green(d)}]: "
    try:
        value = input(display).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{yellow('Setup cancelled.')}")
        sys.exit(1)
    if not value:
        return default
    return value.startswith("y")


def prompt_int(prompt_text: str, default: int, min_val: int = 0,
               max_val: int = 999999) -> int:
    """Prompt for an integer value within range."""
    while True:
        try:
            value = input(f"{prompt_text} [{green(str(default))}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{yellow('Setup cancelled.')}")
            sys.exit(1)
        if not value:
            return default
        try:
            ival = int(value)
            if min_val <= ival <= max_val:
                return ival
            print(red(f"  ⚠ Value must be between {min_val} and {max_val}."))
        except ValueError:
            print(red(f"  ⚠ Please enter a valid number."))


# ── Interactive sections ───────────────────────────────────────────

def setup_engine_config() -> dict:
    """Configure basic engine settings."""
    print(cyan("\n── Engine Configuration ────────────────────────────"))

    cfg = load_config("engine", {})
    print("  Controls how the YuniScripts engine starts and runs.")

    fresh = cfg.get("fresh_install", True)
    if prompt_boolean("  Treat this as a fresh install?", fresh):
        cfg["fresh_install"] = True
    else:
        cfg["fresh_install"] = False

    reset_venvs = cfg.get("reset_venvs", False)
    if prompt_boolean("  Reset virtual environments on next start?", reset_venvs):
        cfg["reset_venvs"] = True
    else:
        cfg["reset_venvs"] = False

    save_config("engine", cfg)
    print(green("  ✓ Engine config saved."))
    return cfg


def setup_ah_config() -> dict:
    """Configure the Auction House economy system."""
    print(cyan("\n── Auction House / AI Economy ──────────────────────"))

    cfg = load_config("ah", {})

    print("  The AI economy uses DeepSeek to simulate a living market.")
    print("  Without an API key, the economy will use basic defaults.\n")

    api_key = cfg.get("deepseek_api_key", "")
    cfg["deepseek_api_key"] = prompt_mandatory(
        "  DeepSeek API Key",
        default=api_key,
        secret=True
    )

    model = cfg.get("deepseek_model", "deepseek-chat")
    cfg["deepseek_model"] = prompt_optional(
        "  DeepSeek Model",
        default=model
    )

    interval = cfg.get("simulation_interval_minutes", 360)
    cfg["simulation_interval_minutes"] = prompt_int(
        "  Market simulation interval (minutes)",
        default=interval, min_val=5, max_val=1440
    )

    save_config("ah", cfg)
    print(green("  ✓ AH config saved."))
    return cfg


def setup_minecraft_manager_config() -> dict:
    """Configure RCON connection to the Minecraft server."""
    print(cyan("\n── Minecraft Server (RCON) ─────────────────────────"))

    cfg = load_config("minecraft_manager", {})

    print("  RCON allows YuniScripts to control your Minecraft server.\n")

    cfg["rcon_host"] = prompt_optional(
        "  RCON Host",
        default=cfg.get("rcon_host", "127.0.0.1")
    )

    cfg["rcon_port"] = prompt_int(
        "  RCON Port",
        default=int(cfg.get("rcon_port", 25575)),
        min_val=1, max_val=65535
    )

    cfg["rcon_password"] = prompt_mandatory(
        "  RCON Password",
        default=cfg.get("rcon_password", ""),
        secret=True
    )

    save_config("minecraft_manager", cfg)
    print(green("  ✓ Minecraft Manager config saved."))
    return cfg


def setup_simulated_people_config() -> dict:
    """Configure the simulated persona system."""
    print(cyan("\n── Simulated People (Personas) ─────────────────────"))

    cfg = load_config("simulated_people", {})

    print("  Personas simulate players in the economy.")
    print("  More personas = more activity but higher API costs.\n")

    tiers = ["ultra_data_saver", "data_saver", "normal",
             "above_normal", "burn_my_tokens"]
    tier_desc = {
        "ultra_data_saver": "1-3 personas (minimal API cost)",
        "data_saver": "7 personas (low cost, DEFAULT)",
        "normal": "20 personas (medium cost)",
        "above_normal": "50 personas (high cost)",
        "burn_my_tokens": "200 personas (maximum!)",
    }

    current_tier = cfg.get("persona_tier", "data_saver")
    print("  Select a persona tier:")
    for i, t in enumerate(tiers, 1):
        marker = green(" ★") if t == current_tier else ""
        print(f"    {i}. {t:20s} — {tier_desc[t]}{marker}")

    while True:
        try:
            choice = input(f"  Tier [default: {green(current_tier)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{yellow('Setup cancelled.')}")
            sys.exit(1)
        if not choice:
            choice = tiers.index(current_tier) + 1
            break
        try:
            choice = int(choice)
            if 1 <= choice <= len(tiers):
                break
        except ValueError:
            if choice in tiers:
                break
        print(red(f"  ⚠ Choose 1-{len(tiers)} or a tier name."))

    selected_tier = tiers[choice - 1] if isinstance(choice, int) else choice
    cfg["persona_tier"] = selected_tier

    cfg["ai_persona_decision"] = prompt_boolean(
        "  Use AI for persona decisions? (costs API tokens)",
        default=cfg.get("ai_persona_decision", True)
    )

    save_config("simulated_people", cfg)
    print(green("  ✓ Simulated People config saved."))
    return cfg


def setup_eco_config() -> dict:
    """Configure the economy bridge."""
    print(cyan("\n── Economy Bridge ──────────────────────────────────"))

    cfg = load_config("eco", {})

    print("  Connects to an external economy (e.g. OttersCiv).\n")

    cfg["ooga_db_path"] = prompt_optional(
        "  Database path (relative or absolute)",
        default=cfg.get("ooga_db_path", "config/otters_civ_revived/project_ooga.db")
    )

    cfg["eco_bridge_host"] = prompt_optional(
        "  Remote bridge host (leave empty to disable)",
        default=cfg.get("eco_bridge_host", "")
    )

    if cfg["eco_bridge_host"]:
        cfg["eco_bridge_port"] = prompt_int(
            "  Remote bridge port",
            default=int(cfg.get("eco_bridge_port", 7200)),
            min_val=1, max_val=65535
        )
        cfg["eco_bridge_password"] = prompt_mandatory(
            "  Remote bridge password",
            default=cfg.get("eco_bridge_password", ""),
            secret=True
        )

    save_config("eco", cfg)
    print(green("  ✓ Economy Bridge config saved."))
    return cfg


def setup_steam_nextcloud_config() -> dict:
    """Configure Steam + Nextcloud integration."""
    print(cyan("\n── Steam & Nextcloud Integration (optional) ───────"))

    cfg = load_config("steam_nextcloud", {})

    if not prompt_boolean("  Configure Steam+Nextcloud?", default=bool(cfg)):
        return cfg

    cfg["steam_api_key"] = prompt_optional(
        "  Steam API Key",
        default=cfg.get("steam_api_key", "")
    )
    cfg["steam_id"] = prompt_optional(
        "  Steam ID",
        default=cfg.get("steam_id", "")
    )
    cfg["nextcloud_url"] = prompt_optional(
        "  Nextcloud URL",
        default=cfg.get("nextcloud_url", "")
    )

    save_config("steam_nextcloud", cfg)
    print(green("  ✓ Steam+Nextcloud config saved."))
    return cfg


# ── Summary ────────────────────────────────────────────────────────

def print_summary():
    """Print a summary of where all configs live."""
    data_dir = get_data_dir()
    print(cyan("\n═══ Setup Complete ═══════════════════════════════\n"))
    print(green("  Your config files are in:"))
    print(f"    {data_dir}/")
    print()

    # List all configs that were saved
    for name, filename in [
        ("engine", "engine_config.json"),
        ("minecraft_manager", "minecraft_manager_config.json"),
        ("ah", "ah_config.json"),
        ("simulated_people", "simulated_people_config.json"),
        ("eco", "eco_config.json"),
        ("item_signing_bridge", "item_signing_bridge_config.json"),
        ("server_stats_collector", "server_stats_collector_config.json"),
        ("steam_nextcloud", "steam_nextcloud_config.ini"),
    ]:
        path = data_dir / filename
        status = green("✓") if path.exists() else yellow("✗")
        title = name.replace("_", " ").title()
        print(f"  {status}  {title:20s} → {filename}")

    print()
    print(cyan("  To change settings, edit the files above and restart."))
    print(cyan("  To run this wizard again:"))
    print(cyan("    python3 SCRIPTS/TOOLS/first-run-setup/main.py --force"))
    print()


# ── Main ───────────────────────────────────────────────────────────

def main():
    force = "--force" in sys.argv

    if not is_fresh_install() and not force:
        print(green("\n  ✓ YuniScripts is already configured."))
        print("  To re-run setup: python3 SCRIPTS/TOOLS/first-run-setup/main.py --force")
        print("SHUTDOWN_COMPLETE", flush=True)
        return 0

    signal.signal(signal.SIGINT, lambda s, f: sys.exit(1))

    print(BANNER)

    setup_engine_config()
    setup_minecraft_manager_config()
    setup_ah_config()
    setup_simulated_people_config()
    setup_eco_config()
    setup_steam_nextcloud_config()

    # Mark setup complete
    mark_setup_complete()

    print_summary()
    print(green("  Enjoy YuniScripts! 🚀"))
    print()
    print("SHUTDOWN_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
