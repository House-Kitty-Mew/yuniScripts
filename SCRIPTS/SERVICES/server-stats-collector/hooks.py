"""Hook registration for server-stats-collector."""
def on_stats_received_callback(state):
    # This runs inside the script process, so we can just print or log
    print(f"Hook: stats received at {state['timestamp']}")
    # You can add custom actions here (e.g., send alert, write to file)
    return state

def register_hooks(hook_registry):
    # The engine will call this, but inside the subprocess we can't modify
    # the engine's registry. Instead, we return the hook function so main.py can call it.
    return hook_registry