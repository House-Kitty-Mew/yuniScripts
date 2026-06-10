"""Pure functional hook registry and caller."""
def create_hook_registry():
    """Return a fresh empty hook registry."""
    return {}

def register_hook(registry, hook_name, callback):
    """Add a callback to a hook, returning a new registry."""
    new_registry = {**registry}
    new_registry[hook_name] = new_registry.get(hook_name, []) + [callback]
    return new_registry

def call_hooks(registry, hook_name, engine_state):
    """
    Execute all callbacks for a hook.
    engine_state is a dict with keys like 'registry', 'running', etc.
    Each callback receives engine_state and returns a new engine_state.
    Returns the final engine_state after all callbacks have run.
    """
    callbacks = registry.get(hook_name, [])
    state = engine_state
    for cb in callbacks:
        state = cb(state)
    return state