#!/usr/bin/env python3
# deepsky_admin_monitor.py - Standalone CLI for DeepSky monitoring

import argparse, json, os, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

HOME = Path.home()
DEFAULT_DB_PATHS = [HOME / "Documents" / "dev-yuniScripts" / "DATA" / "Databases" / "Documentation.db", HOME / "AIHandler" / "SCRIPTS" / "DatabaseHandler" / "DATA" / "Databases" / "Documentation.db"]
DEFAULT_LOG_PATHS = [HOME / "Documents" / "dev-yuniScripts" / "SCRIPTS" / "CLIENTS" / "deepsky_client" / "deepsky_client.log", HOME / "Documents" / "dev-yuniScripts" / "trash" / "engine" / "logs", HOME / "Documents" / "dev-yuniScripts" / "trash" / "engine_output.log"]

def find_database(override=None):
    if override: p = Path(override); return str(p) if p.exists() else None
    try:
        sys.path.insert(0, os.path.expanduser("~/Documents/dev-yuniScripts/SCRIPTS/SERVICES/fastmcp_server"))
        from ecosystem_config import get_documentation_db_path
        ep = get_documentation_db_path()
        if os.path.exists(ep): return ep
    except Exception: pass
    for dp in DEFAULT_DB_PATHS:
        if dp.exists(): return str(dp)
    return None

def find_log_file(override=None):
    if override: p = Path(override); return str(p) if p.exists() else None
    for lp in DEFAULT_LOG_PATHS:
        if lp.exists(): return str(lp)
    return None

def get_work_order_stats(db_path):
    s = {"total":0,"pending":0,"in_progress":0,"completed":0,"blocked":0,"failed":0,"archived":0,"created_last_hour":0,"completed_last_hour":0,"p1_pending":0,"p2_pending":0}
    try:
        conn = sqlite3.connect(db_path); cur = conn.cursor()
        cur.execute("SELECT status,COUNT(*) FROM work_orders GROUP BY status")
        for r in cur.fetchall(): k = r[0] if r[0] in s else "other"; s[k] = r[1]; s["total"] += r[1]
        h1 = (datetime.now(timezone.utc)-timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("SELECT COUNT(*) FROM work_orders WHERE created_at>=?",(h1,))
        s["created_last_hour"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM work_orders WHERE status='completed' AND completed_at>=?",(h1,))
        s["completed_last_hour"] = cur.fetchone()[0]
        cur.execute("SELECT priority,COUNT(*) FROM work_orders WHERE status='pending' GROUP BY priority")
        for r in cur.fetchall(): s["p"+str(r[0])+"_pending"] = r[1]
        conn.close()
    except Exception as e: s["error"]=str(e)
    return s

def get_active_orders(db_path):
    orders = []
    try:
        conn = sqlite3.connect(db_path); cur = conn.cursor()
        cur.execute("SELECT id,priority,status,description,created_at,updated_at,assigned_to FROM work_orders WHERE status IN ('pending','in_progress','blocked') ORDER BY priority ASC,id DESC")
        for r in cur.fetchall():
            d = (r[3] or "")[:120]
            orders.append({"id":r[0],"priority":r[1],"status":r[2],"description":d,"created_at":r[4],"updated_at":r[5],"assigned_to":r[6]})
        conn.close()
    except Exception as e: orders.append({"error":str(e)})
    return orders

def get_agent_history(db_path,limit=10):
    history = []
    try:
        conn = sqlite3.connect(db_path); cur = conn.cursor()
        cur.execute("SELECT id,priority,status,description,completed_at,notes FROM work_orders WHERE status='completed' AND notes IS NOT NULL AND notes LIKE '%[AUTO-HEALING]%' ORDER BY COALESCE(completed_at,updated_at) DESC LIMIT ?",(limit,))
        for r in cur.fetchall():
            d = (r[3] or "")[:80]
            history.append({"id":r[0],"priority":r[1],"status":r[2],"description":d,"completed_at":r[4],"notes_preview":(r[5] or "")[:100]})
        conn.close()
    except Exception as e: history.append({"error":str(e)})
    return history

def get_system_info(db_path):
    info = {"db_path":db_path,"db_size_bytes":os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0}
    try:
        conn = sqlite3.connect(db_path); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM work_orders WHERE notes LIKE '%[AUTO-HEALING]%'")
        info["auto_healing_events"] = cur.fetchone()[0]
        cur.execute("SELECT MAX(created_at) FROM work_orders")
        info["last_work_order_created"] = cur.fetchone()[0]
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_usage'")
        if cur.fetchone():
            cur.execute("SELECT COUNT(*) FROM tool_usage"); info["total_tool_calls"] = cur.fetchone()[0]
        else: info["total_tool_calls"] = 0
        conn.close()
    except Exception as e: info["error"]=str(e)
    return info

def tail_log(log_path,lines=20):
    if not log_path or not os.path.exists(log_path): return ["[LOG NOT FOUND: "+str(log_path)+"]"]
    if os.path.isdir(log_path):
        lfs = sorted([os.path.join(log_path,f) for f in os.listdir(log_path) if f.endswith(".log")], key=os.path.getmtime, reverse=True)
        if not lfs: return ["[NO LOG FILES IN: "+str(log_path)+"]"]
        log_path = lfs[0]
    if not os.path.isfile(log_path): return ["[NOT A FILE: "+str(log_path)+"]"]
    try:
        with open(log_path) as f: content = f.read()
        all_lines = content.split(chr(10))
        return [l.rstrip(chr(13)) for l in all_lines[-lines:]]
    except Exception as e: return ["[ERROR READING LOG: "+str(e)+"]"]

# Colors
CR = chr(27)+"[0m"; CG = chr(27)+"[92m"; CY = chr(27)+"[93m"
CRED = chr(27)+"[91m"; CC = chr(27)+"[96m"; CB = chr(27)+"[1m"; CD = chr(27)+"[2m"

def _c(c,t):
    if not sys.stdout.isatty(): return t
    return c + t + CR

def _sc(s):
    return {"pending":CY,"in_progress":CC,"completed":CG,"blocked":CRED,"failed":CRED,"archived":CD}.get(s,CR)

def print_dashboard(stats,orders,history,sysinfo,log_lines=None):
    try:
        w=72; ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    except Exception as e:
        logger.error(f"print_dashboard failed: {e}")
        return None
    print(); print(_c(CB,chr(0x2554)+chr(0x2550)*(w-2)+chr(0x2557)))
    print(_c(CB,chr(0x2551))+_c(CC,"  DEEPSKY ADMIN MONITOR  ".center(w-4))+_c(CB,chr(0x2551)))
    print(_c(CB,chr(0x2551))+_c(CD,("  Auto Pilot Dashboard  "+ts).center(w-4))+_c(CB,chr(0x2551)))
    print(_c(CB,chr(0x255A)+chr(0x2550)*(w-2)+chr(0x255D))); print()
    print(_c(CB,"WORK ORDER SUMMARY")); print("-"*w)
    print("  Total: "+str(stats.get("total",0))+"  "+_c(CY,"Pending: "+str(stats.get("pending",0))).ljust(18)+"  "+_c(CC,"In Progress: "+str(stats.get("in_progress",0))))
    print("  "+_c(CG,"Completed: "+str(stats.get("completed",0))).ljust(22)+"  "+_c(CRED,"Blocked: "+str(stats.get("blocked",0))).ljust(18)+"  Failed: "+str(stats.get("failed",0)))
    print()
    print("  Created(1h): "+str(stats.get("created_last_hour",0))+"  Completed(1h): "+str(stats.get("completed_last_hour",0)))
    if "total_tool_calls" in sysinfo: print("  Tool calls: "+str(sysinfo["total_tool_calls"]))
    print()
    print(_c(CB,"ACTIVE WORK ORDERS")); print("-"*w)
    for o in orders:
        pv=o.get("priority",3); pl={1:"P1",2:"P2",3:"P3",4:"P4",5:"P5"}.get(pv,"P"+str(pv))
        sc=_sc(o.get("status","")); st=_c(sc,o.get("status","").ljust(12))
        print("  #"+str(o.get("id","?"))[:5].ljust(5)+" ["+pl+"] "+st+" "+((o.get("description","")or"")[:55]))
    print()
    print(_c(CB,"AGENT HISTORY")); print("-"*w)
    if not history: print("  No completed auto-healing sessions.")
    else:
        for h in history:
            print("  #"+str(h["id"]).ljust(5)+" "+_c(CG,"COMPLETED").ljust(12)+" P"+str(h.get("priority","?"))+" "+((h.get("description","")or"")[:50]))
    print()
    print(_c(CB,"SYSTEM")); print("-"*w)
    print("  DB: "+sysinfo.get("db_path","N/A"))
    print("  Size: {:.1f} MB".format(sysinfo.get("db_size_bytes",0)/1024/1024))
    print("  Last WO: "+str(sysinfo.get("last_work_order_created","N/A")))
    print()
    if log_lines:
        print(_c(CB,"RECENT LOGS")); print("-"*w)
        for l in log_lines[-10:]:
            print("  "+_c(CD,l[:w-4]))
        print()

def print_json_output(stats,orders,history,sysinfo,log_lines=None):
    import json
    print(json.dumps({"timestamp":datetime.now(timezone.utc).isoformat(),"work_order_stats":stats,"active_orders":orders,"agent_history":history,"system_info":sysinfo,"recent_logs":log_lines[-10:]if log_lines else[]},indent=2,default=str))

def print_history_only(history):
    print(); print(_c(CB,"DEEPSKY AGENT HISTORY")); print("="*60)
    if not history: print("  No completed sessions.")
    else:
        for h in history:
            print("  #"+str(h["id"]).ljust(5)+" "+_c(CG,"COMPLETED").ljust(12)+" P"+str(h.get("priority","?"))+"  "+((h.get("description","")or"")[:60]))
    print()

def watch_mode(db_path,log_path,interval=3):
    try:
        while True:
            if sys.stdout.isatty(): os.system("clear")
            print_dashboard(get_work_order_stats(db_path),get_active_orders(db_path),get_agent_history(db_path,5),get_system_info(db_path),tail_log(log_path or "",15))
            print(_c(CD,"  [--watch] Refresh every "+str(interval)+"s. Ctrl+C to stop."))
            time.sleep(interval)
    except KeyboardInterrupt: print()

def parse_args(argv=None):
    p=argparse.ArgumentParser(description="DeepSky Admin Monitor")
    p.add_argument("--status",action="store_true"); p.add_argument("--watch",action="store_true")
    p.add_argument("--history",action="store_true"); p.add_argument("--json",action="store_true")
    p.add_argument("--db",type=str,default=None); p.add_argument("--log",type=str,default=None)
    return p.parse_args(argv)

def main(argv=None):
    try:
        args=parse_args(argv); db_path=find_database(args.db)

    except Exception as e:
        logger.error(f"main failed: {e}")
        return None
    if not db_path:
        print("ERROR: Could not locate Documentation.db")
        if args.db: print("  Tried: "+args.db+" (not found)")
        for dp in DEFAULT_DB_PATHS: print("  Tried: "+str(dp))
        print("Use --db PATH"); return 1
    log_path=find_log_file(args.log)
    if args.history: print_history_only(get_agent_history(db_path,20)); return 0
    if args.watch: watch_mode(db_path,log_path); return 0
    s=get_work_order_stats(db_path); o=get_active_orders(db_path)
    h=get_agent_history(db_path,8); si=get_system_info(db_path); l=tail_log(log_path or "",20)
    if args.json: print_json_output(s,o,h,si,l)
    else: print_dashboard(s,o,h,si,l)
    return 0

if __name__=="__main__": sys.exit(main())

