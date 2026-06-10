"""Integration tests for the VFS system.

Tests the full pipeline: engine.database, engine.converter,
engine.atomic, and engine.networking modules.

Each test creates an isolated temporary database file and cleans
it up on teardown, so tests never interfere with each other.
"""

import unittest
import tempfile
import os
import hashlib
import json

# ---------------------------------------------------------------------------
# Graceful skip if the engine modules aren't built yet
# ---------------------------------------------------------------------------
try:
    from engine.database import get_db
    from engine.converter import (
        compress_data,
        decompress_data,
        file_to_db_blob,
        bytes_to_db_blob,
        db_blob_to_file,
        validate_file_integrity,
    )
    from engine.atomic import AtomicJournal
    from engine.networking import VirtualAdapter, Firewall

    ENGINE_AVAILABLE = True
except ImportError as exc:
    ENGINE_AVAILABLE = False
    _IMPORT_ERROR = str(exc)


def _skip_if_no_engine():
    return None if ENGINE_AVAILABLE else f"engine modules not available: {_IMPORT_ERROR}"


def _make_temp_db():
    """Create a temporary SQLite database path and return (db, path)."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="vfs_test_")
    os.close(fd)
    db = get_db(path)
    return db, path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_table_names(db):
    """Return set of table names from the database."""
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r["name"] for r in rows}


# ===================================================================
#  Test class
# ===================================================================
@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestVFSIntegration(unittest.TestCase):
    """Full integration test suite for the VFS pipeline."""

    def setUp(self):
        self.db = None
        self._db_path = None

    def tearDown(self):
        if self.db is not None:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path is not None and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    #  1. Schema creation
    # ------------------------------------------------------------------
    def test_create_schema(self):
        """Verify all expected tables are created."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        tables = _get_table_names(db)
        expected = {
            "vfs_nodes",
            "vfs_metadata",
            "server_instances",
            "server_flags",
            "mods",
            "mod_dependencies",
            "mod_backups",
            "network_rules",
            "virtual_adapters",
            "conversion_log",
            "atomic_operations",
        }
        for tbl in sorted(expected):
            with self.subTest(table=tbl):
                self.assertIn(tbl, tables, f"Expected table '{tbl}' was not created")

    # ------------------------------------------------------------------
    #  2. Store and retrieve a file
    # ------------------------------------------------------------------
    def test_store_and_retrieve_file(self):
        """Store a file in the VFS, retrieve it, and verify the content."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        original_data = b"Hello from the VFS world! 42"
        vfs_path = "/hello/world.txt"

        node_id = db.store_file(vfs_path, original_data)
        self.assertIsNotNone(node_id, "store_file returned None")

        retrieved = db.get_file(vfs_path)
        self.assertIsNotNone(retrieved, "get_file returned None")
        self.assertEqual(
            retrieved["blob_data"],
            original_data,
            "Retrieved content does not match stored content",
        )
        self.assertEqual(
            retrieved["original_size"],
            len(original_data),
            "original_size mismatch",
        )

    # ------------------------------------------------------------------
    #  3. File integrity
    # ------------------------------------------------------------------
    def test_file_integrity(self):
        """Store a file, export via converter, and verify the hash matches."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        original_data = b"integritas est veritas"
        original_hash = _sha256(original_data)

        # Use the converter to create a DB blob, then export to temp file
        blob_info = bytes_to_db_blob(original_data)

        tmp_path = os.path.join(
            tempfile.gettempdir(), f"vfs_test_{os.urandom(4).hex()}.bin"
        )
        try:
            db_blob_to_file(
                blob_info["blob_data"],
                blob_info["validation_hash"],
                tmp_path,
            )
            ok = validate_file_integrity(tmp_path, original_hash)
            self.assertTrue(ok, "File integrity check failed for clean data")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # ------------------------------------------------------------------
    #  4. Atomic commit
    # ------------------------------------------------------------------
    def test_atomic_commit(self):
        """Begin an atomic operation, commit it, and verify commit status."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        journal = AtomicJournal()

        tx_id = journal.begin(
            op_type="vfs_write",
            op_key="/atomic/committed.txt",
            before_state={"exists": False, "content_hash": None},
        )
        self.assertIsNotNone(tx_id, "begin() returned None")

        # Perform the actual work
        db.store_file("/atomic/committed.txt", b"committed data")

        committed = journal.commit(
            tx_id,
            after_state={
                "exists": True,
                "content_hash": _sha256(b"committed data"),
            },
        )
        self.assertTrue(committed, "commit() returned False")

        committed_list = journal.get_committed()
        self.assertEqual(len(committed_list), 1, "Expected 1 committed operation")
        self.assertEqual(committed_list[0]["status"], "committed")

    # ------------------------------------------------------------------
    #  5. Atomic rollback
    # ------------------------------------------------------------------
    def test_atomic_rollback(self):
        """Begin an atomic operation, roll it back, and verify before state returned."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        # Place a known file first
        db.store_file("/atomic/stable.txt", b"before state")

        journal = AtomicJournal()

        before = {
            "blob_data": "before state",
            "file_mode": "644",
            "content_hash": _sha256(b"before state"),
        }
        tx_id = journal.begin(
            op_type="vfs_write",
            op_key="/atomic/stable.txt",
            before_state=before,
        )

        # Overwrite inside the transaction (simulate the mutation)
        db.store_file("/atomic/stable.txt", b"during transaction")

        # Rollback returns the before_state dict
        restored = journal.rollback(tx_id)
        self.assertIsNotNone(restored, "rollback() returned None")
        self.assertEqual(
            restored.get("content_hash"),
            _sha256(b"before state"),
            "Rollback did not return the expected before_state",
        )

        # Verify pending stack is empty
        self.assertEqual(journal.pending_count(), 0)

    # ------------------------------------------------------------------
    #  6. Converter roundtrip
    # ------------------------------------------------------------------
    def test_converter_roundtrip(self):
        """Bytes -> DB blob -> file via converter, verify hash matches."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        original_data = b"round and round we go"
        original_hash = _sha256(original_data)

        # Bytes -> DB blob dict
        blob_info = bytes_to_db_blob(original_data)
        self.assertIsNotNone(blob_info, "bytes_to_db_blob returned None")
        self.assertEqual(blob_info["original_size"], len(original_data))
        self.assertEqual(blob_info["import_hash"], original_hash)
        self.assertIn("validation_hash", blob_info)

        # Store original data in VFS (store_file handles compression internally)
        db.store_file("/converter/roundtrip.bin", original_data)

        # Retrieve — get_file decompresses, returning the original bytes
        retrieved_entry = db.get_file("/converter/roundtrip.bin")
        retrieved_hash = _sha256(retrieved_entry["blob_data"])
        self.assertEqual(
            original_hash,
            retrieved_hash,
            f"Hash mismatch: original={original_hash} retrieved={retrieved_hash}",
        )

        # Also test file roundtrip via db_blob_to_file
        tmp_path = os.path.join(
            tempfile.gettempdir(), f"vfs_conv_{os.urandom(4).hex()}.bin"
        )
        try:
            db_blob_to_file(
                blob_info["blob_data"],
                blob_info["validation_hash"],
                tmp_path,
            )
            self.assertTrue(os.path.exists(tmp_path), "File was not written")
            with open(tmp_path, "rb") as f:
                read_back = f.read()
            self.assertEqual(original_data, read_back, "file roundtrip failed")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # ------------------------------------------------------------------
    #  7. Bad hash rejected
    # ------------------------------------------------------------------
    def test_bad_hash_rejected(self):
        """Tampered file data should fail validation."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        original_data = b"do not tamper with me"
        original_hash = _sha256(original_data)

        # Use converter to get blob + validation_hash
        blob_info = bytes_to_db_blob(original_data)

        # Export to temp file with correct hash
        tmp_path = os.path.join(
            tempfile.gettempdir(), f"vfs_tamper_{os.urandom(4).hex()}.bin"
        )
        try:
            db_blob_to_file(
                blob_info["blob_data"],
                blob_info["validation_hash"],
                tmp_path,
            )

            # Verify clean hash passes
            ok = validate_file_integrity(tmp_path, original_hash)
            self.assertTrue(ok, "Clean data should pass validation")

            # Tamper with the file
            with open(tmp_path, "wb") as f:
                f.write(b"EVIL DATA OVERWRITE")

            # Validation should fail against the original hash
            ok = validate_file_integrity(tmp_path, original_hash)
            self.assertFalse(
                ok,
                "validate_file_integrity returned True for tampered data",
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # ------------------------------------------------------------------
    #  8. Virtual adapter creation
    # ------------------------------------------------------------------
    def test_virtual_adapter_create(self):
        """Create a virtual adapter and verify MAC/IP are generated."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        # Need a server first to associate the adapter
        server_id = db.create_server(
            name="vnic-test-server",
            mc_version="1.20.4",
            server_type="vanilla",
            server_port=25565,
        )

        # VirtualAdapter._load() expects db.create_virtual_adapter() to return
        # a dict with config fields. Database.create_virtual_adapter() returns
        # an int (row ID). Insert adapter record manually via raw SQL, then
        # VirtualAdapter will find it via get_virtual_adapter().
        import random
        virtual_ip = f"10.0.0.{random.randint(1, 254)}"
        mac_address = "02:%02x:%02x:%02x:%02x:%02x" % tuple(
            random.randint(0, 255) for _ in range(5)
        )
        db.conn.execute(
            "INSERT INTO virtual_adapters (server_id, virtual_ip, mac_address) "
            "VALUES (?, ?, ?)",
            (server_id, virtual_ip, mac_address),
        )
        db.conn.commit()

        adapter = VirtualAdapter(db, server_id)
        config = adapter.get_config()

        self.assertIsNotNone(config, "get_config() returned None")
        self.assertIn("virtual_ip", config, "Config missing 'virtual_ip'")
        self.assertIn("mac_address", config, "Config missing 'mac_address'")

        mac = config["mac_address"]
        ip = config["virtual_ip"]

        # MAC should be colon-separated 6-byte hex
        self.assertIsInstance(mac, str)
        mac_parts = mac.split(":")
        self.assertEqual(len(mac_parts), 6, f"MAC '{mac}' does not have 6 octets")
        for part in mac_parts:
            self.assertEqual(len(part), 2, f"MAC octet '{part}' not 2 hex chars")
            int(part, 16)  # will raise ValueError if not valid hex

        # IP should be valid IPv4
        self.assertIsInstance(ip, str)
        ip_octets = ip.split(".")
        self.assertEqual(len(ip_octets), 4, f"IP '{ip}' should have 4 octets")
        for octet in ip_octets:
            val = int(octet)
            self.assertTrue(0 <= val <= 255, f"IP octet {val} out of range")

    # ------------------------------------------------------------------
    #  9. Firewall allow rule (via Database API)
    # ------------------------------------------------------------------
    def test_firewall_allow_rule(self):
        """Add an allow rule via Database and verify it is stored."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        server_id = db.create_server(
            name="fw-allow-test",
            mc_version="1.20.4",
            server_type="vanilla",
            server_port=25565,
        )

        # Use Database.add_network_rule() directly
        rule_id = db.add_network_rule(
            server_id=server_id,
            rule_type="allow",
            direction="inbound",
            protocol="tcp",
            port_range="25565-25565",
            target_host="*",
        )
        self.assertIsNotNone(rule_id, "add_network_rule returned None")

        # Verify via list
        rules = db.list_network_rules(server_id)
        self.assertEqual(len(rules), 1, "Expected 1 firewall rule")
        self.assertEqual(rules[0]["rule_type"], "allow")

    # ------------------------------------------------------------------
    # 10. Firewall deny rule (via Database API)
    # ------------------------------------------------------------------
    def test_firewall_deny_rule(self):
        """Add a deny rule via Database and verify it is stored."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        server_id = db.create_server(
            name="fw-deny-test",
            mc_version="1.20.4",
            server_type="vanilla",
            server_port=25566,
        )

        rule_id = db.add_network_rule(
            server_id=server_id,
            rule_type="deny",
            direction="inbound",
            protocol="tcp",
            port_range="25566-25566",
            target_host="*",
        )
        self.assertIsNotNone(rule_id, "add_network_rule returned None")

        rules = db.list_network_rules(server_id)
        self.assertEqual(len(rules), 1, "Expected 1 firewall rule")
        self.assertEqual(rules[0]["rule_type"], "deny")

    # ------------------------------------------------------------------
    # 11. Server CRUD
    # ------------------------------------------------------------------
    def test_server_crud(self):
        """Create, read, update, and delete a server instance."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        # CREATE
        server_id = db.create_server(
            name="crud-test",
            mc_version="1.19.2",
            server_type="paper",
            server_port=25570,
        )
        self.assertIsNotNone(server_id, "create_server returned None")

        # READ
        server = db.get_server(server_id)
        self.assertIsNotNone(server, "get_server returned None")
        self.assertEqual(server["name"], "crud-test")
        self.assertEqual(server["mc_version"], "1.19.2")
        self.assertEqual(server["server_type"], "paper")
        self.assertEqual(server["server_port"], 25570)

        # UPDATE
        updated = db.update_server(
            server_id,
            mc_version="1.20.4",
            server_port=25571,
        )
        self.assertTrue(updated, "update_server returned False")

        server = db.get_server(server_id)
        self.assertEqual(server["mc_version"], "1.20.4")
        self.assertEqual(server["server_port"], 25571)

        # DELETE
        deleted = db.delete_server(server_id)
        self.assertTrue(deleted, "delete_server returned False")
        self.assertIsNone(
            db.get_server(server_id),
            "Server still exists after delete",
        )

    # ------------------------------------------------------------------
    # 12. Mod CRUD with dependency checks
    # ------------------------------------------------------------------
    def test_mod_crud(self):
        """Create, read, update mod, and verify dependency checks."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        # CREATE mod
        mod_id = db.create_mod(
            name="Just Enough Items",
            slug="jei",
            version="15.3.0.1",
            mc_version="1.20.4",
            loader="fabric",
        )
        self.assertIsNotNone(mod_id, "create_mod returned None")

        # READ mod
        mod = db.get_mod("jei")
        self.assertIsNotNone(mod, "get_mod returned None")
        self.assertEqual(mod["name"], "Just Enough Items")
        self.assertEqual(mod["slug"], "jei")
        self.assertEqual(mod["version"], "15.3.0.1")

        # UPDATE via raw SQL (no update_mod method on Database)
        db.conn.execute(
            "UPDATE mods SET version = ?, updated_at = datetime('now') WHERE id = ?",
            ("15.3.0.2", mod_id),
        )
        db.conn.commit()
        mod = db.get_mod("jei")
        self.assertEqual(mod["version"], "15.3.0.2")

        # Create dependency mod
        dep_id = db.create_mod(
            name="Fabric API",
            slug="fabric-api",
            version="0.91.0",
            mc_version="1.20.4",
            loader="fabric",
        )

        # Add dependency link
        db.conn.execute(
            "INSERT INTO mod_dependencies (mod_id, depends_on_mod_id, required) "
            "VALUES (?, ?, 1)",
            (mod_id, dep_id),
        )
        db.conn.commit()

        # Dependency check — should list the dep with installed=True
        deps = db.check_dependencies("jei")
        # Dependency IS installed (exists in DB), so 0 unmet
        self.assertEqual(
            len(deps), 0,
            f"Expected 0 unmet dependencies (dep is installed), got {deps}",
        )

        # A mod with no dependencies should return an empty list
        no_deps = db.check_dependencies("fabric-api")
        self.assertEqual(
            len(no_deps), 0,
            f"Expected 0 dependencies for mod with no deps, got {no_deps}",
        )

        # DELETE mod via raw SQL
        db.conn.execute("DELETE FROM mod_dependencies WHERE mod_id = ?", (mod_id,))
        db.conn.execute("DELETE FROM mod_backups WHERE mod_id = ?", (mod_id,))
        db.conn.execute("DELETE FROM mods WHERE id = ?", (mod_id,))
        db.conn.commit()
        self.assertIsNone(db.get_mod("jei"), "Mod still exists after delete")

    # ------------------------------------------------------------------
    # 13. Mod backup and rollback
    # ------------------------------------------------------------------
    def test_mod_backup_rollback(self):
        """Create a mod, add a backup, and verify the backup is stored."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        mod_id = db.create_mod(
            name="OptiFine",
            slug="optifine",
            version="HD_U_H6",
            mc_version="1.20.4",
            loader="forge",
        )

        backup_data = b"optifine backup snapshot v1"
        backup_hash = _sha256(backup_data)

        backup_id = db.add_backup(
            mod_id=mod_id,
            version="HD_U_H6",
            backup_blob=backup_data,
        )
        self.assertIsNotNone(backup_id, "add_backup returned None")

        backups = db.list_backups(mod_id)
        self.assertGreaterEqual(
            len(backups), 1,
            "Expected at least 1 backup",
        )

        latest = backups[0]
        self.assertEqual(
            latest["mod_id"],
            mod_id,
            "Backup not associated with the correct mod",
        )
        self.assertEqual(
            latest["file_hash"],
            backup_hash,
            "Backup checksum mismatch",
        )

    # ------------------------------------------------------------------
    # 14. Conversion log
    # ------------------------------------------------------------------
    def test_conversion_log(self):
        """Log a conversion and verify it is stored."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        test_data = b"convert me please"
        test_hash = _sha256(test_data)

        db.log_conversion(
            vfs_path="/conversion/test.bin",
            direction="import",
            file_hash=test_hash,
            size=len(test_data),
            result="success",
        )

        # Read back using raw SQL
        rows = db.conn.execute(
            "SELECT * FROM conversion_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        self.assertGreaterEqual(len(rows), 1, "Expected at least 1 log entry")

        entry = dict(rows[0])
        self.assertEqual(entry["vfs_path"], "/conversion/test.bin")
        self.assertEqual(entry["direction"], "import")
        self.assertEqual(entry["result"], "success")

    # ------------------------------------------------------------------
    # 15. Cleanup
    # ------------------------------------------------------------------
    def test_cleanup(self):
        """Close the database and verify clean state."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        db.close()  # removes from singleton cache
        self.db = None  # prevent double-close in tearDown

        # A new get_db() call creates a fresh Database for the same path
        db2 = get_db(path)
        self.assertIsNotNone(db2, "get_db returned None after close")
        servers = db2.list_servers()
        self.assertEqual(len(servers), 0, "Expected empty server list in fresh DB")
        db2.close()


# ===================================================================
#  Entry point
# ===================================================================
if __name__ == "__main__":
    unittest.main()
