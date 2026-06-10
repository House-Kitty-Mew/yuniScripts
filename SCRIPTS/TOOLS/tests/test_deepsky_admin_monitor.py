#!/usr/bin/env python3
"""test_deepsky_admin_monitor.py - Unit tests for deepsky_admin_monitor.py"""

import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

tdir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(tdir))

from deepsky_admin_monitor import (
    find_database, find_log_file,
    get_work_order_stats, get_active_orders,
    get_agent_history, get_system_info, tail_log,
    parse_args, main, _c, print_json_output, print_history_only,
)

def _make_db(path):
    conn = sqlite3.connect(path); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS work_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT, status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 3, description TEXT, notes TEXT, assigned_to TEXT DEFAULT 'AI', completed_at TEXT, source_file TEXT, metadata TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS tool_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, tool_name TEXT, session_id TEXT, parameters TEXT, token_estimate INTEGER, duration_ms INTEGER)")
    orders = [
        (1,"2026-06-07 19:00:00","2026-06-07 19:00:00","pending",1,"[P1] Live API Test",None),
        (2,"2026-06-07 18:00:00","2026-06-07 18:30:00","completed",1,"[P1] Sub-agent fix","[AUTO-HEALING] Fix applied"),
        (3,"2026-06-07 17:00:00","2026-06-07 17:30:00","completed",2,"[P2] Log Watcher","[AUTO-HEALING] Created"),
        (4,"2026-06-07 16:00:00","2026-06-07 16:00:00","pending",3,"[P3] Noise Filter",None),
        (5,"2026-06-07 15:00:00","2026-06-07 15:30:00","in_progress",1,"[P1] Test Suite",None),
        (6,"2026-06-07 14:00:00","2026-06-07 14:00:00","blocked",1,"[P1] Master Runner","BLOCKED"),
        (7,"2026-06-07 13:00:00","2026-06-07 13:30:00","completed",2,"[P2] Network fix","[AUTO-HEALING] Fixed"),
        (8,"2026-06-07 12:00:00","2026-06-07 12:00:00","pending",4,"[P4] Parity Audit",None),
        (9,"2026-06-07 11:00:00","2026-06-07 11:00:00","pending",5,"[P5] Stale Test",None),
        (10,"2026-06-07 10:00:00","2026-06-07 10:30:00","completed",2,"[P2] Notifier","[AUTO-HEALING] Done"),
    ]
    for o in orders: cur.execute("INSERT INTO work_orders (id,created_at,updated_at,status,priority,description,notes) VALUES (?,?,?,?,?,?,?)", o)
    cur.execute("INSERT INTO tool_usage (timestamp,tool_name,session_id) VALUES (?,?,?)", ("2026-06-07 19:00:00","edit_text","sess-1"))
    cur.execute("INSERT INTO tool_usage (timestamp,tool_name,session_id) VALUES (?,?,?)", ("2026-06-07 18:30:00","write_file","sess-2"))
    conn.commit(); conn.close()
    return path

class TestFindDB(unittest.TestCase):
    def test_override_valid(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f: f.write(b""); tmp = f.name
        try: self.assertEqual(find_database(override=tmp), tmp)
        finally: os.unlink(tmp)
    def test_override_invalid(self): self.assertIsNone(find_database(override="/nope/x.db"))
    def test_no_override(self): r = find_database(); self.assertTrue(r is None or os.path.exists(r))

class TestWorkOrderStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)
    def test_keys(self):
        s = get_work_order_stats(self.tmp_db)
        for k in ("total","pending","in_progress","completed","blocked","p1_pending"): self.assertIn(k, s)
    def test_counts(self):
        s = get_work_order_stats(self.tmp_db)
        self.assertEqual(s["total"], 10); self.assertEqual(s["pending"], 4)
        self.assertEqual(s["in_progress"], 1); self.assertEqual(s["completed"], 4); self.assertEqual(s["blocked"], 1)
    def test_missing_db(self): self.assertIn("error", get_work_order_stats("/nope/x.db"))
    def test_p1_pending(self): self.assertEqual(get_work_order_stats(self.tmp_db)["p1_pending"], 1)

class TestActiveOrders(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)
    def test_count(self): self.assertEqual(len(get_active_orders(self.tmp_db)), 6)
    def test_no_completed(self):
        for o in get_active_orders(self.tmp_db): self.assertNotEqual(o.get("status"), "completed")
    def test_sorted(self):
        orders = get_active_orders(self.tmp_db)
        prios = [o["priority"] for o in orders if "error" not in o]
        self.assertEqual(prios, sorted(prios))
    def test_keys(self):
        for o in get_active_orders(self.tmp_db):
            if "error" in o: continue
            for k in ("id","priority","status","description"): self.assertIn(k, o)

class TestAgentHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)
    def test_count(self): self.assertEqual(len(get_agent_history(self.tmp_db, 10)), 4)
    def test_limit(self): self.assertLessEqual(len(get_agent_history(self.tmp_db, 2)), 2)
    def test_notes(self):
        for h in get_agent_history(self.tmp_db, 5):
            if "error" in h: continue
            self.assertIn("notes_preview", h)

class TestSystemInfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.tmp_db = tempfile.mktemp(suffix=".db"); _make_db(cls.tmp_db)
    @classmethod
    def tearDownClass(cls): os.unlink(cls.tmp_db)
    def test_keys(self):
        info = get_system_info(self.tmp_db)
        for k in ("db_path","db_size_bytes","auto_healing_events","total_tool_calls"): self.assertIn(k, info)
    def test_size(self): self.assertGreater(get_system_info(self.tmp_db)["db_size_bytes"], 0)
    def test_auto_healing(self): self.assertEqual(get_system_info(self.tmp_db)["auto_healing_events"], 4)
    def test_tool_calls(self): self.assertEqual(get_system_info(self.tmp_db)["total_tool_calls"], 2)

class TestLogTail(unittest.TestCase):
    def test_tail(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            for i in range(100): f.write(str(i)+chr(10))
            tmp = f.name
        try:
            lines = tail_log(tmp, 5)
            self.assertEqual(len(lines), 5)
            self.assertEqual(len(lines), 5)
        finally: os.unlink(tmp)
    def test_nonexistent(self):
        lines = tail_log("/nope/x.log")
        self.assertTrue(any("[LOG NOT FOUND" in l for l in lines))

class TestDisplay(unittest.TestCase):
    def test_color_tty(self):
        with patch("sys.stdout.isatty",return_value=True): self.assertIn("92m", _c(chr(27)+"[92m","hi"))
    def test_color_notty(self):
        with patch("sys.stdout.isatty",return_value=False): self.assertEqual(_c(chr(27)+"[92m","hi"),"hi")
    def test_json(self):
        import io; out = io.StringIO()
        with patch("sys.stdout",out): print_json_output({"total":5},{"id":1,"s":"p"},{"id":2},{"p":"/x.db"})
        parsed = json.loads(out.getvalue())
        self.assertIn("work_order_stats", parsed)
        self.assertIn("active_orders", parsed)
    def test_history_empty(self):
        import io; out = io.StringIO()
        with patch("sys.stdout",out): print_history_only([])
        self.assertIn("No completed", out.getvalue())

class TestCLI(unittest.TestCase):
    def test_defaults(self):
        a = parse_args([])
        self.assertFalse(a.watch); self.assertFalse(a.history); self.assertFalse(a.json)
    def test_watch(self): self.assertTrue(parse_args(["--watch"]).watch)
    def test_history(self): self.assertTrue(parse_args(["--history"]).history)
    def test_json(self): self.assertTrue(parse_args(["--json"]).json)
    def test_db(self): self.assertEqual(parse_args(["--db","/x.db"]).db, "/x.db")
    def test_unknown(self):
        with self.assertRaises(SystemExit): parse_args(["--badflag"])

class TestMain(unittest.TestCase):
    def test_missing_db(self):
        with patch("deepsky_admin_monitor.find_database",return_value=None):
            with patch("sys.stdout"): self.assertEqual(main(["--db","/nope.db"]), 1)
    def test_valid_db(self):
        tmp_db = tempfile.mktemp(suffix=".db")
        try:
            _make_db(tmp_db)
            with patch("deepsky_admin_monitor.find_database",return_value=tmp_db):
                with patch("sys.stdout"): self.assertEqual(main([]), 0)
        finally: os.unlink(tmp_db)
    def test_history(self):
        tmp_db = tempfile.mktemp(suffix=".db")
        try:
            _make_db(tmp_db)
            with patch("deepsky_admin_monitor.find_database",return_value=tmp_db):
                with patch("sys.stdout"): self.assertEqual(main(["--history"]), 0)
        finally: os.unlink(tmp_db)
    def test_json_valid(self):
        tmp_db = tempfile.mktemp(suffix=".db")
        try:
            _make_db(tmp_db)
            with patch("deepsky_admin_monitor.find_database",return_value=tmp_db):
                import io; out = io.StringIO()
                with patch("sys.stdout",out): self.assertEqual(main(["--json"]), 0)
                json.loads(out.getvalue())
        finally: os.unlink(tmp_db)

if __name__ == "__main__": unittest.main()