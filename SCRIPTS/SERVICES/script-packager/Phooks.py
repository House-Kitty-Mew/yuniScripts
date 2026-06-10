# Phooks.py — Script Packager Phooks event declarations
# Defines which inter-script events this service listens to and emits.

PHOOKS_EVENTS_LISTEN = [
    # ── Script Snapshot Lifecycle ──
    "packager.snapshot.create",       # Create a snapshot datagram of a script
    "packager.snapshot.load",         # Load/import a snapshot datagram
    "packager.snapshot.list",         # List available snapshots
    "packager.snapshot.delete",       # Delete a stored snapshot
    "packager.snapshot.info",         # Get info about a specific snapshot
    
    # ── Script Discovery ──
    "packager.script.list",           # List all scriptable scripts
    "packager.script.config.get",     # Get a script's compile/decompile config
    "packager.script.config.set",     # Set a script's compile/decompile config
    
    # ── GUI Integration ──
    "packager.gui.register",          # GUI Dashboard requests tab registration
    "packager.gui.status",            # GUI Dashboard requests status update
    
    # ── Deploy/Import ──
    "packager.deploy.preview",        # Preview what deploying a datagram would do
    "packager.deploy.execute",        # Execute a datagram deployment into the engine
]

PHOOKS_EVENTS_EMIT = [
    # ── Responses ──
    "packager.response.snapshot",
    "packager.response.script",
    "packager.response.config",
    "packager.response.deploy",
    "packager.response.gui",
    
    # ── Notifications ──
    "packager.notify.snapshot_created",
    "packager.notify.snapshot_loaded",
    "packager.notify.deploy_complete",
    "packager.notify.error",
]