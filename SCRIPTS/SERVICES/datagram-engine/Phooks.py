# Phooks.py — Datagram Engine Phooks event declarations
# Required by the YuniScripts engine. Defines which inter-script events
# this script listens to and emits.

# Events this script LISTENS for:
# Other scripts send these to request datagram operations.
PHOOKS_EVENTS_LISTEN = [
    # ── Datagram Lifecycle ──
    "datagram.create",          # Create a new datagram
    "datagram.load",            # Load an existing datagram
    "datagram.delete",          # Delete/corrupt a datagram
    "datagram.validate",        # Validate datagram structure
    
    # ── Metadata Operations ──
    "datagram.meta.get",        # Get datagram metadata
    "datagram.meta.update",     # Update datagram metadata
    
    # ── Hash/Integrity ──
    "datagram.hash.compute",    # Compute content hash
    "datagram.hash.verify",     # Verify content hash
    "datagram.hash.update",     # Update stored hash
    
    # ── Database CRUD ──
    "datagram.db.connect",      # Connect to a datagram database
    "datagram.db.insert",       # Insert a record
    "datagram.db.select",       # Query records
    "datagram.db.update",       # Update records
    "datagram.db.delete",       # Delete records
    
    # ── Embedded Functions ──
    "datagram.func.load",       # Load an embedded function
    "datagram.func.execute",    # Execute an embedded function
    "datagram.func.list",       # List available functions
    
    # ── Compatibility ──
    "datagram.compat.check",    # Check version compatibility
    "datagram.compat.register", # Register component version
]

# Events this script EMITS:
# Other scripts can listen for these responses and notifications.
PHOOKS_EVENTS_EMIT = [
    # ── Responses ──
    "datagram.response.create",
    "datagram.response.load",
    "datagram.response.delete",
    "datagram.response.validate",
    "datagram.response.meta",
    "datagram.response.hash",
    "datagram.response.db",
    "datagram.response.func",
    "datagram.response.compat",
    
    # ── Notifications ──
    "datagram.notify.created",
    "datagram.notify.loaded",
    "datagram.notify.modified",
    "datagram.notify.corrupted",
    "datagram.notify.error",
]
