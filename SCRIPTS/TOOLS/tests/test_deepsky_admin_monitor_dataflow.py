#!/usr/bin/env python3
"""test_deepsky_admin_monitor_dataflow.py - Data flow validation tests."""

import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

tdir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(tdir))

from deepsky_admin_monitor import (
    find_database, get_work_order_stats, get_active_orders,
    get_agent_history, get_system_info, tail_log,
    main, print_dashboard, print_json_output, print_history_only,
)


def _make_db(path):
    conn = sqlite3.connect(path); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS work_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 3, description TEXT, notes TEXT, assigned_to TEXT DEFAULT 'AI', completed_at TEXT, source_file TEXT, metadata TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS tool_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, tool_name TEXT, session_id TEXT, parameters TEXT, token_estimate INTEGER, duration_ms INTEGER)")
    for i in range(1,6):
        cur.execute("INSERT INTO work_orders (id,created_at,updated_at,status,priority,description,notes) VALUES (?,?,?,?,?,?,?)",(i,"2026-06-07 19:0"+str(i)+":00","2026-06-07 19:0"+str(i)+":30","pending" if i%2 else "completed",1 if i<3 else 2,"Test WO #"+str(i),"[AUTO-HEALING] Fixed" if i%2==0 else None))
    cur.execute("INSERT INTO tool_usage VALUES (1,'now','read','s1','{}',100,50)")
    conn.commit(); conn.close()
    return path


class TestDataFlow_DBtoStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)

    def test_flow_db_to_stats(self):
        stats = get_work_order_stats(self.tmp_db)
        self.assertIn("total", stats); self.assertIn("pending", stats)
        self.assertGreater(stats["total"], 0)

    def test_flow_stats_values(self):
        stats = get_work_order_stats(self.tmp_db)
        self.assertEqual(stats["total"], 5)

    def test_flow_db_to_active(self):
        orders = get_active_orders(self.tmp_db)
        self.assertEqual(len(orders), 3)
        for o in orders: self.assertNotEqual(o.get("status"), "completed")

    def test_flow_db_to_history(self):
        history = get_agent_history(self.tmp_db, 10)
        self.assertEqual(len(history), 2)

    def test_flow_db_to_sysinfo(self):
        info = get_system_info(self.tmp_db)
        self.assertGreater(info.get("db_size_bytes", 0), 0)
        self.assertGreaterEqual(info.get("auto_healing_events", 0), 2)

    def test_flow_db_to_tool_calls(self):
        info = get_system_info(self.tmp_db)
        self.assertGreaterEqual(info.get("total_tool_calls", 0), 1)


class TestDataFlow_ErrorPaths(unittest.TestCase):
    def test_flow_missing_db_stats(self):
        stats = get_work_order_stats("/nope/db.db")
        self.assertIn("error", stats)

    def test_flow_missing_db_active(self):
        orders = get_active_orders("/nope/db.db")
        self.assertTrue(len(orders) == 0 or "error" in orders[0])

    def test_flow_missing_db_history(self):
        history = get_agent_history("/nope/db.db", 5)
        self.assertTrue(len(history) == 0 or "error" in history[0])

    def test_flow_missing_log(self):
        lines = tail_log("/nope/log.log")
        self.assertTrue(any("[LOG NOT FOUND" in l for l in lines))

    def test_flow_main_missing_db(self):
        with patch("deepsky_admin_monitor.find_database", return_value=None):
            with patch("sys.stdout"):
                self.assertEqual(main(["--db", "/nope/x.db"]), 1)


class TestDataFlow_DisplayPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)

    def test_flow_dashboard_no_crash(self):
        import io; out = io.StringIO()
        with patch("sys.stdout", out):
            print_dashboard(get_work_order_stats(self.tmp_db), get_active_orders(self.tmp_db), get_agent_history(self.tmp_db,5), get_system_info(self.tmp_db), ["log1"])
        self.assertGreater(len(out.getvalue()), 0)

    def test_flow_json_valid(self):
        import io; out = io.StringIO()
        with patch("sys.stdout", out):
            print_json_output(get_work_order_stats(self.tmp_db), get_active_orders(self.tmp_db), get_agent_history(self.tmp_db,5), get_system_info(self.tmp_db))
        parsed = json.loads(out.getvalue())
        self.assertIn("work_order_stats", parsed)

    def test_flow_history_no_crash(self):
        import io; out = io.StringIO()
        with patch("sys.stdout", out):
            print_history_only(get_agent_history(self.tmp_db,5))
        self.assertGreater(len(out.getvalue()), 0)


class TestDataFlow_CLIPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)

    def test_flow_cli_status(self):
        with patch("deepsky_admin_monitor.find_database", return_value=self.tmp_db):
            with patch("sys.stdout"):
                self.assertEqual(main([]), 0)

    def test_flow_cli_json(self):
        with patch("deepsky_admin_monitor.find_database", return_value=self.tmp_db):
            import io; out = io.StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["--json"]), 0)
            parsed = json.loads(out.getvalue())
            self.assertEqual(parsed["work_order_stats"]["total"], 5)

    def test_flow_cli_history(self):
        with patch("deepsky_admin_monitor.find_database", return_value=self.tmp_db):
            with patch("sys.stdout"):
                self.assertEqual(main(["--history"]), 0)


if __name__ == "__main__": unittest.main()
