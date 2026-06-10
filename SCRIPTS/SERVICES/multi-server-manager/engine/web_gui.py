"""
Multi-Server Manager — Web GUI (Phase 5)

Provides a browser-based dashboard for managing servers, plugins,
databases, and monitoring in real-time.

DESIGN:
  - Zero external dependencies (uses stdlib asyncio + simple HTTP parsing)
  - REST API backed by AdminCLI dispatch methods
  - Single-page application (SPA) frontend embedded as HTML string
  - Graceful degradation: port conflicts log warnings without crashing

API ENDPOINTS:
  GET  /                     — Serve SPA frontend
  GET  /api/status           — Global status overview
  GET  /api/servers          — List all servers
  GET  /api/servers/{id}     — Server info
  POST /api/servers/{id}/start  — Start a server
  POST /api/servers/{id}/stop   — Stop a server
  POST /api/servers/{id}/restart— Restart a server
  GET  /api/plugins          — List all plugins
  GET  /api/plugins/{name}   — Plugin info
  GET  /api/databases        — List VFS databases
  GET  /api/databases/{subsys}/{sid} — Database details
  GET  /api/database-health  — Health check all databases
  GET  /api/logs             — Recent events
  GET  /api/config           — Show config
  POST /api/config/reload    — Reload config
  GET  /api/instances        — List plugin instances
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("web_gui")


# ═════════════════════════════════════════════════════════════════════════════
# SPA Frontend (embedded)
# ═════════════════════════════════════════════════════════════════════════════

SPA_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Multi-Server Manager</title>
<style>
  :root {
    --bg: #0d1117;
    --bg-card: #161b22;
    --bg-hover: #1c2333;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --orange: #d8860b;
    --rad: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  .app { max-width: 1400px; margin: 0 auto; padding: 20px; }
  header { display: flex; align-items: center; justify-content: space-between;
           padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
  header h1 { font-size: 22px; font-weight: 600; }
  header .subtitle { font-size: 13px; color: var(--text-dim); }
  .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
                margin-right: 6px; }
  .status-dot.green { background: var(--green); }
  .status-dot.red { background: var(--red); }
  .status-dot.yellow { background: var(--yellow); }
  .nav { display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap; }
  .nav button { background: var(--bg-card); color: var(--text); border: 1px solid var(--border);
                padding: 8px 18px; border-radius: var(--rad); cursor: pointer;
                font-size: 13px; transition: 0.15s; }
  .nav button:hover { background: var(--bg-hover); border-color: var(--accent); }
  .nav button.active { border-color: var(--accent); background: #0d2d5a; }
  .dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
               gap: 16px; margin-bottom: 24px; }
  .card { background: var(--bg-card); border: 1px solid var(--border);
          border-radius: var(--rad); padding: 16px; }
  .card h3 { font-size: 13px; color: var(--text-dim); text-transform: uppercase;
             letter-spacing: 0.5px; margin-bottom: 8px; }
  .card .value { font-size: 28px; font-weight: 600; }
  .card .sub { font-size: 12px; color: var(--text-dim); margin-top: 4px; }
  .section { margin-bottom: 24px; }
  .section h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
           font-size: 13px; }
  th { color: var(--text-dim); font-weight: 500; text-transform: uppercase;
       letter-spacing: 0.5px; font-size: 11px; }
  tr:hover { background: var(--bg-hover); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 11px; font-weight: 500; }
  .badge.running { background: #0d3d1a; color: var(--green); }
  .badge.stopped { background: #3d0d0d; color: var(--red); }
  .badge.degraded { background: #3d2d0d; color: var(--yellow); }
  .badge.online { background: #0d3d1a; color: var(--green); }
  .badge.offline { background: #3d0d0d; color: var(--red); }
  .badge.healthy { background: #0d3d1a; color: var(--green); }
  .badge.unhealthy { background: #3d0d0d; color: var(--red); }
  .btn { padding: 6px 14px; border: 1px solid var(--border); border-radius: var(--rad);
         background: transparent; color: var(--text); cursor: pointer; font-size: 12px;
         transition: 0.15s; }
  .btn:hover { background: var(--bg-hover); }
  .btn.start { border-color: var(--green); color: var(--green); }
  .btn.start:hover { background: #0d3d1a; }
  .btn.stop { border-color: var(--red); color: var(--red); }
  .btn.stop:hover { background: #3d0d0d; }
  .btn.restart { border-color: var(--yellow); color: var(--yellow); }
  .btn.restart:hover { background: #3d2d0d; }
  .error { color: var(--red); font-size: 13px; padding: 12px; }
  .loading { text-align: center; padding: 40px; color: var(--text-dim); }
  .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: var(--rad);
           font-size: 13px; z-index: 999; max-width: 400px; transition: 0.3s; }
  .toast.success { background: #0d3d1a; border: 1px solid var(--green); color: var(--green); }
  .toast.error { background: #3d0d0d; border: 1px solid var(--red); color: var(--red); }
  .mb-1 { margin-bottom: 8px; }
  .flex { display: flex; align-items: center; gap: 8px; }
  pre { background: #0d1117; padding: 12px; border-radius: var(--rad);
        font-size: 12px; overflow-x: auto; max-height: 400px; border: 1px solid var(--border); }
</style>
</head>
<body>
<div class="app" id="app">
  <header>
    <div>
      <h1>🖥️ Multi-Server Manager</h1>
      <div class="subtitle">Phase 5 Web Dashboard</div>
    </div>
    <div class="flex">
      <span class="status-dot" id="connDot" title="Connection status"></span>
      <span id="connStatus" style="font-size:13px;color:var(--text-dim)">Connecting...</span>
    </div>
  </header>
  <nav class="nav" id="navBar">
    <button class="active" data-tab="overview">📊 Overview</button>
    <button data-tab="servers">🖧 Servers</button>
    <button data-tab="plugins">🔌 Plugins</button>
    <button data-tab="databases">🗄️ Databases</button>
    <button data-tab="logs">📜 Logs</button>
    <button data-tab="config">⚙️ Config</button>
  </nav>
  <div id="content"><div class="loading">⏳ Loading...</div></div>
</div>
<script>
const API = '';
let polling = false;

function toast(msg, type='success') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function api(url, opts={}) {
  const res = await fetch(API + url, opts);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

function updateConn(ok) {
  document.getElementById('connDot').className = 'status-dot ' + (ok ? 'green' : 'red');
  document.getElementById('connStatus').textContent = ok ? 'Connected' : 'Disconnected';
}

// ── Tab System ──────────────────────────────────────────────────────
let currentTab = 'overview';

document.getElementById('navBar').addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentTab = btn.dataset.tab;
  renderTab(currentTab);
});

async function renderTab(tab) {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading">Loading...</div>';
  try {
    const data = await fetchTabData(tab);
    el.innerHTML = renderers[tab](data);
  } catch(e) {
    el.innerHTML = '<div class="error">Error: ' + e.message + '</div>';
  }
}

async function fetchTabData(tab) {
  switch(tab) {
    case 'overview': return api('/api/status');
    case 'servers': return api('/api/servers');
    case 'plugins': return api('/api/plugins');
    case 'databases': return api('/api/databases');
    case 'logs': return api('/api/logs');
    case 'config': return api('/api/config');
  }
}

// ── Renderers ───────────────────────────────────────────────────────
const renderers = {};

renderers.overview = function(d) {
  if (!d || !d.data) return '<div class="error">No data available</div>';
  const s = d.data;
  const healthy = s.servers_healthy || 0;
  const total = s.servers_total || 0;
  return `
    <div class="dashboard">
      <div class="card">
        <h3>Servers</h3>
        <div class="value">${total}</div>
        <div class="sub">${healthy} healthy / ${total - healthy} degraded</div>
      </div>
      <div class="card">
        <h3>Plugins</h3>
        <div class="value">${s.plugins_total || 0}</div>
        <div class="sub">${s.plugins_registered || 0} registered</div>
      </div>
      <div class="card">
        <h3>Databases</h3>
        <div class="value">${s.databases_total || 0}</div>
        <div class="sub">${s.databases_healthy || 0} healthy</div>
      </div>
      <div class="card">
        <h3>Events</h3>
        <div class="value">${s.events_total || 0}</div>
        <div class="sub">Last event: ${s.last_event_time || '-'}</div>
      </div>
    </div>
    <div class="section">
      <h2>🔍 Quick Actions</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn start" onclick="actionAll('start')">▶ Start All</button>
        <button class="btn stop" onclick="actionAll('stop')">⬛ Stop All</button>
        <button class="btn" onclick="refreshAll()">🔄 Refresh</button>
      </div>
    </div>
    ${s.plugins_list ? '<div class="section"><h2>Plugin Health</h2><table><tr><th>Plugin</th><th>Servers</th><th>Status</th></tr>' +
      s.plugins_list.map(p => '<tr><td>' + p.name + '</td><td>' + (p.servers||'-') + '</td><td><span class="badge ' + p.health + '">' + p.health + '</span></td></tr>').join('') +
    '</table></div>' : ''}
  `;
};

renderers.servers = function(d) {
  if (!d || !d.data) return '<div class="error">No server data</div>';
  const servers = Array.isArray(d.data) ? d.data : [d.data];
  return `
    <div class="section"><h2>🖧 Server List</h2>
    <table>
      <tr><th>ID</th><th>Name</th><th>State</th><th>Plugins</th><th>Actions</th></tr>
      ${servers.map(s => '<tr><td>' + (s.server_id||s.id||'-') + '</td><td>' + (s.display_name||s.name||'-') + '</td>' +
        '<td><span class="badge ' + (s.state||'stopped') + '">' + (s.state||'stopped') + '</span></td>' +
        '<td>' + (s.plugin_count||0) + '</td>' +
        '<td><button class="btn start" onclick="actionServer(\'' + (s.server_id||s.id) + '\',\'start\')">▶</button> ' +
        '<button class="btn stop" onclick="actionServer(\'' + (s.server_id||s.id) + '\',\'stop\')">⬛</button> ' +
        '<button class="btn restart" onclick="actionServer(\'' + (s.server_id||s.id) + '\',\'restart\')">🔄</button></td></tr>').join('')}
    </table></div>
    <div class="section"><h2>📋 Server Details</h2>
    ${servers.map(s => '<div class="card mb-1"><pre>' + JSON.stringify(s, null, 2) + '</pre></div>').join('')}</div>
  `;
};

renderers.plugins = function(d) {
  if (!d || !d.data) return '<div class="error">No plugin data</div>';
  const plugins = Array.isArray(d.data) ? d.data : [d.data];
  return `
    <div class="section"><h2>🔌 Registered Plugins</h2>
    <table>
      <tr><th>Name</th><th>Version</th><th>Description</th><th>Deps</th></tr>
      ${plugins.map(p => '<tr><td><strong>' + p.name + '</strong></td><td>' + (p.version||'-') + '</td>' +
        '<td>' + (p.description||'-') + '</td><td>' + ((p.dependencies||[]).join(', ') || 'none') + '</td></tr>').join('')}
    </table></div>
    <div class="section"><h2>📋 Plugin Details</h2>
    <div class="card"><pre>' + JSON.stringify(plugins, null, 2) + '</pre></div></div>
  `;
};

renderers.databases = function(d) {
  if (!d || !d.data) return '<div class="error">No database data</div>';
  const dbs = Array.isArray(d.data) ? d.data : [];
  const health = d.health || {};
  return `
    <div class="section"><h2>🗄️ VFS Databases</h2>
    <table>
      <tr><th>Subsystem</th><th>Server</th><th>Size</th><th>Tables</th><th>Healthy</th></tr>
      ${dbs.length ? dbs.map(db => '<tr><td>' + (db.subsystem||'-') + '</td><td>' + (db.server_id||'-') + '</td>' +
        '<td>' + (db.size||'0 B') + '</td><td>' + (db.table_count||0) + '</td>' +
        '<td><span class="badge ' + (db.is_healthy ? 'healthy' : 'unhealthy') + '">' + (db.is_healthy ? '✓' : '✗') + '</span></td></tr>').join('') :
        '<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">No databases</td></tr>'}
    </table></div>
    <div class="section"><h2>🩺 Database Health</h2>
    <div class="card"><pre>' + JSON.stringify(health, null, 2) + '</pre></div></div>
  `;
};

renderers.logs = function(d) {
  if (!d || !d.data) return '<div class="error">No log data</div>';
  const events = Array.isArray(d.data) ? d.data : [];
  return `
    <div class="section">
      <div class="flex" style="margin-bottom:12px">
        <h2 style="margin:0">📜 Recent Events</h2>
        <button class="btn" onclick="renderTab('logs')">🔄 Refresh</button>
      </div>
      <table>
        <tr><th>Time</th><th>Type</th><th>Server</th><th>Plugin</th><th>Message</th></tr>
        ${events.length ? events.map(e => '<tr><td style="font-size:11px">' + (e.timestamp||e.created_at||'-') + '</td>' +
          '<td><span class="badge ' + (e.success !== false ? '' : 'unhealthy') + '">' + (e.event_type||e.type||'event') + '</span></td>' +
          '<td>' + (e.server_id||'-') + '</td>' +
          '<td>' + (e.plugin_name||'-') + '</td>' +
          '<td>' + (e.message||'-') + '</td></tr>').join('') :
          '<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">No events</td></tr>'}
      </table>
    </div>
  `;
};

renderers.config = function(d) {
  if (!d || !d.data) return '<div class="error">No config data</div>';
  return `
    <div class="section">
      <div class="flex" style="margin-bottom:12px">
        <h2 style="margin:0">⚙️ Configuration</h2>
        <button class="btn" onclick="reloadConfig()">🔄 Reload</button>
      </div>
      <div class="card"><pre>' + JSON.stringify(d.data, null, 2) + '</pre></div>
    </div>
  `;
};

// ── Actions ─────────────────────────────────────────────────────────
async function actionServer(id, action) {
  try {
    const res = await api('/api/servers/' + encodeURIComponent(id) + '/' + action, { method: 'POST' });
    toast(action + ' ' + id + ': ' + (res.message || 'OK'), 'success');
    if (currentTab === 'servers') renderTab('servers');
  } catch(e) {
    toast(action + ' ' + id + ': ' + e.message, 'error');
  }
}

async function actionAll(action) {
  try {
    const servers = await api('/api/servers');
    const list = Array.isArray(servers.data) ? servers.data : [];
    for (const s of list) {
      await api('/api/servers/' + encodeURIComponent(s.server_id || s.id) + '/' + action, { method: 'POST' });
    }
    toast(action + ' all servers: done', 'success');
    if (currentTab === 'servers') renderTab('servers');
  } catch(e) {
    toast(action + ' all: ' + e.message, 'error');
  }
}

async function reloadConfig() {
  try {
    await api('/api/config/reload', { method: 'POST' });
    toast('Config reloaded', 'success');
    if (currentTab === 'config') renderTab('config');
  } catch(e) {
    toast('Reload failed: ' + e.message, 'error');
  }
}

async function refreshAll() {
  renderTab(currentTab);
}

// ── Polling for connection status ───────────────────────────────────
async function checkConn() {
  try {
    const r = await fetch(API + '/api/status');
    updateConn(r.ok);
  } catch {
    updateConn(false);
  }
}

// ── Init ────────────────────────────────────────────────────────────
(async function() {
  updateConn(await fetch(API + '/api/status').then(r => r.ok).catch(() => false));
  renderTab('overview');
  setInterval(checkConn, 15000);
})();
</script>
</body>
</html>"""


# ═════════════════════════════════════════════════════════════════════════════
# Async HTTP Server
# ═════════════════════════════════════════════════════════════════════════════

class HTTPServer:
    """
    Minimal async HTTP server built on stdlib asyncio.
    
    Routes requests to handler functions, parses path/query/body,
    returns JSON or HTML responses.
    """
    
    def __init__(self, host: str = "localhost", port: int = 8200):
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._routes: Dict[str, Dict[str, Any]] = {}
        self._admin_cli: Any = None
    
    def set_admin_cli(self, cli: Any) -> None:
        """Set the AdminCLI instance for API dispatch."""
        self._admin_cli = cli
    
    def route(self, method: str, path: str):
        """Decorator to register a route handler."""
        def decorator(handler):
            key = f"{method.upper()}:{path}"
            self._routes[key] = handler
            return handler
        return decorator
    
    def _find_route(self, method: str, path: str) -> Tuple[Optional[Any], Dict[str, str]]:
        """
        Find a matching route.
        
        Supports path parameters like {id} and {subsys}/{sid}.
        Returns (handler, path_params).
        """
        method = method.upper()
        
        # Direct match
        key = f"{method}:{path}"
        if key in self._routes:
            return self._routes[key], {}
        
        # Pattern match for path parameters
        for route_key, handler in self._routes.items():
            r_method, r_pattern = route_key.split(":", 1)
            if r_method != method:
                continue
            
            r_parts = r_pattern.strip("/").split("/")
            p_parts = path.strip("/").split("/")
            
            if len(r_parts) != len(p_parts):
                continue
            
            params = {}
            match = True
            for rp, pp in zip(r_parts, p_parts):
                if rp.startswith("{") and rp.endswith("}"):
                    param_name = rp[1:-1]
                    params[param_name] = urllib.parse.unquote(pp)
                elif rp != pp:
                    match = False
                    break
            
            if match:
                return handler, params
        
        return None, {}
    
    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an individual HTTP connection."""
        try:
            # Read request line and headers
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return
            
            request_str = request_line.decode("utf-8", errors="replace").strip()
            if not request_str:
                writer.close()
                return
            
            parts = request_str.split(" ")
            if len(parts) < 2:
                await self._send_response(writer, 400, "Bad Request")
                return
            
            method = parts[0]
            raw_path = parts[1]
            
            # Parse path and query
            parsed = urllib.parse.urlparse(raw_path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            
            # Read headers
            headers = {}
            content_length = 0
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=10)
                if header_line in (b"\r\n", b"\n", b""):
                    break
                h_str = header_line.decode("utf-8", errors="replace").strip()
                if ":" in h_str:
                    h_key, h_val = h_str.split(":", 1)
                    headers[h_key.strip().lower()] = h_val.strip()
                    if h_key.lower() == "content-length":
                        content_length = int(h_val.strip())
            
            # Read body
            body = b""
            if content_length > 0:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=10
                )
            
            # Parse JSON body
            body_data: Any = None
            if body and headers.get("content-type", "").startswith("application/json"):
                try:
                    body_data = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    pass
            
            # Find route
            handler, path_params = self._find_route(method, path)
            
            if handler is None:
                await self._send_response(writer, 404, {
                    "ok": False, "error": f"No route: {method} {path}"
                })
                return
            
            # Call handler
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(method, path, path_params, query, body_data)
                else:
                    result = handler(method, path, path_params, query, body_data)
                await self._send_response(writer, 200, result)
            except Exception as e:
                logger.error("Route handler error: %s", e)
                await self._send_response(writer, 500, {
                    "ok": False, "error": str(e)
                })
        
        except asyncio.TimeoutError:
            try:
                await self._send_response(writer, 408, {"ok": False, "error": "Timeout"})
            except Exception:
                pass
        except Exception as e:
            logger.debug("Client handling error: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass
    
    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        data: Any,
    ) -> None:
        """Send an HTTP response."""
        status_text = {
            200: "OK", 400: "Bad Request", 404: "Not Found",
            408: "Request Timeout", 500: "Internal Server Error",
        }.get(status, "Unknown")
        
        if isinstance(data, str):
            body = data.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif isinstance(data, dict) or isinstance(data, list):
            body = json.dumps(data, indent=2).encode("utf-8")
            content_type = "application/json"
        else:
            body = str(data).encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        
        # Handle CORS for SPA
        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            f"Access-Control-Allow-Headers: Content-Type\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        
        try:
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
    
    async def start(self) -> bool:
        """Start the HTTP server. Returns True if started, False if port taken."""
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self.host,
                port=self.port,
            )
            logger.info("Web GUI server started on http://%s:%d", self.host, self.port)
            return True
        except OSError as e:
            logger.warning(
                "Web GUI could not start on %s:%d (%s) — graceful degradation",
                self.host, self.port, e
            )
            return False
    
    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Web GUI server stopped")


# ═════════════════════════════════════════════════════════════════════════════
# API Handlers
# ═════════════════════════════════════════════════════════════════════════════

class WebGUI:
    """
    Web GUI service combining the HTTP server with AdminCLI dispatch.
    
    Usage:
        gui = WebGUI()
        await gui.start(admin_cli)
    """
    
    def __init__(self, host: str = "localhost", port: int = 8200):
        self.server = HTTPServer(host, port)
        self.admin_cli = None
        self._register_routes()
    
    def _register_routes(self) -> None:
        """Register all API routes."""
        s = self.server
        
        # ── Serve SPA ────────────────────────────────────────────
        @s.route("GET", "/")
        async def _spa(method, path, params, query, body):
            return SPA_HTML
        
        # ── Status ───────────────────────────────────────────────
        @s.route("GET", "/api/status")
        async def _status(method, path, params, query, body):
            return await self._dispatch("global_status")
        
        # ── Servers ──────────────────────────────────────────────
        @s.route("GET", "/api/servers")
        async def _list_servers(method, path, params, query, body):
            return await self._dispatch("list_servers")
        
        @s.route("POST", "/api/servers/{id}/start")
        async def _start_server(method, path, params, query, body):
            return await self._dispatch("start_server", params.get("id"))
        
        @s.route("POST", "/api/servers/{id}/stop")
        async def _stop_server(method, path, params, query, body):
            return await self._dispatch("stop_server", params.get("id"))
        
        @s.route("POST", "/api/servers/{id}/restart")
        async def _restart_server(method, path, params, query, body):
            return await self._dispatch("restart_server", params.get("id"))
        
        @s.route("GET", "/api/servers/{id}")
        async def _server_info(method, path, params, query, body):
            return await self._dispatch("server_info", params.get("id"))
        
        # ── Plugins ──────────────────────────────────────────────
        @s.route("GET", "/api/plugins")
        async def _list_plugins(method, path, params, query, body):
            return await self._dispatch("list_plugins")
        
        @s.route("GET", "/api/plugins/{name}")
        async def _plugin_info(method, path, params, query, body):
            return await self._dispatch("plugin_info", params.get("name"))
        
        # ── Databases ────────────────────────────────────────────
        @s.route("GET", "/api/databases")
        async def _list_databases(method, path, params, query, body):
            return await self._dispatch("list_databases")
        
        @s.route("GET", "/api/databases/{subsys}/{sid}")
        async def _db_detail(method, path, params, query, body):
            return await self._dispatch(
                "show_database", params.get("subsys"), params.get("sid")
            )
        
        @s.route("GET", "/api/database-health")
        async def _db_health(method, path, params, query, body):
            return await self._dispatch("check_database_health")
        
        # ── Logs ─────────────────────────────────────────────────
        @s.route("GET", "/api/logs")
        async def _logs(method, path, params, query, body):
            limit = int(query.get("limit", [20])[0])
            return await self._dispatch("show_events", limit)
        
        # ── Config ───────────────────────────────────────────────
        @s.route("GET", "/api/config")
        async def _config(method, path, params, query, body):
            return await self._dispatch("show_config")
        
        @s.route("POST", "/api/config/reload")
        async def _config_reload(method, path, params, query, body):
            return await self._dispatch("reload_config")
        
        # ── Instances ────────────────────────────────────────────
        @s.route("GET", "/api/instances")
        async def _instances(method, path, params, query, body):
            return await self._dispatch("list_instances")
        
        # ── CORS preflight ───────────────────────────────────────
        @s.route("OPTIONS", "/{path}")
        async def _cors(method, path, params, query, body):
            return {"ok": True}
    
    async def _dispatch(self, method_name: str, *args) -> Any:
        """
        Dispatch to an AdminCLI method.
        
        If AdminCLI is not available, returns a degraded response.
        """
        if self.admin_cli is None:
            return {
                "ok": True,
                "data": self._default_response(method_name),
                "message": "AdminCLI not available — showing default data",
                "degraded": True,
            }
        
        try:
            method = getattr(self.admin_cli, method_name, None)
            if method is None:
                return {"ok": False, "error": f"Unknown method: {method_name}"}
            
            result = await method(*args)
            
            # Convert CLIResult to dict
            if hasattr(result, 'to_dict'):
                return {"ok": True, "data": result.to_dict()}
            elif hasattr(result, 'data'):
                return {"ok": True, "data": result.data}
            elif isinstance(result, dict):
                return {"ok": True, "data": result}
            else:
                return {"ok": True, "data": str(result)}
        
        except Exception as e:
            logger.error("Dispatch error for %s: %s", method_name, e)
            return {
                "ok": False,
                "error": str(e),
                "method": method_name,
                "degraded": True,
            }
    
    def _default_response(self, method_name: str) -> Any:
        """Return default/sample data when AdminCLI is unavailable."""
        defaults = {
            "global_status": {
                "servers_total": 0, "servers_healthy": 0,
                "plugins_total": 0, "plugins_registered": 0,
                "databases_total": 0, "databases_healthy": 0,
                "events_total": 0, "last_event_time": "-",
                "plugins_list": [],
            },
            "list_servers": [],
            "list_plugins": [],
            "list_databases": [],
            "show_config": {},
            "show_events": [],
            "list_instances": [],
            "check_database_health": {"healthy": True, "total": 0, "unhealthy": []},
        }
        return defaults.get(method_name, {"message": "Not available"})
    
    async def start(self, admin_cli: Any = None) -> bool:
        """
        Start the Web GUI server.
        
        Args:
            admin_cli: Optional AdminCLI instance for API dispatch.
        
        Returns:
            True if started successfully, False if port was taken.
        """
        self.admin_cli = admin_cli
        self.server.set_admin_cli(admin_cli)
        return await self.server.start()
    
    async def stop(self) -> None:
        """Stop the Web GUI server."""
        await self.server.stop()


# ═════════════════════════════════════════════════════════════════════════════
# Convenience
# ═════════════════════════════════════════════════════════════════════════════

async def run_gui(
    admin_cli: Any = None,
    host: str = "localhost",
    port: int = 8200,
) -> WebGUI:
    """
    Create and start a Web GUI server.
    
    This is a convenience function for integration into main.py.
    
    Args:
        admin_cli: Optional AdminCLI instance
        host: Bind host
        port: Bind port
    
    Returns:
        WebGUI instance (check if started via return value)
    """
    gui = WebGUI(host=host, port=port)
    started = await gui.start(admin_cli)
    if not started:
        logger.warning("Web GUI failed to start on %s:%d (graceful degradation)", host, port)
    return gui
