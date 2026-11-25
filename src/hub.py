"""Deskflow-Tailscale Fleet Hub: HTTP server, SQLite state, REST API, and Web UI."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import layout_compiler  # noqa: E402
import qr_gen  # noqa: E402
import sysinfo  # noqa: E402

_REPO_ROOT = _SRC_DIR.parent
_WEB_INDEX = _REPO_ROOT / "web" / "index.html"


def default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "deskflow-tailscale"
    return Path.home() / ".local" / "share" / "deskflow-tailscale"


class FleetStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    machine_key TEXT UNIQUE NOT NULL,
                    computer_name TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    os TEXT NOT NULL,
                    os_detail TEXT NOT NULL,
                    tailscale_ip TEXT NOT NULL,
                    dns_name TEXT NOT NULL,
                    display_w INTEGER NOT NULL,
                    display_h INTEGER NOT NULL,
                    monitors_json TEXT NOT NULL,
                    deskflow_installed INTEGER NOT NULL,
                    deskflow_status TEXT NOT NULL,
                    deskflow_log TEXT NOT NULL,
                    server_conf_path TEXT NOT NULL,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS layout_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    revision INTEGER NOT NULL,
                    master_id TEXT,
                    items_json TEXT NOT NULL,
                    active_preset TEXT
                );
                CREATE TABLE IF NOT EXISTS presets (
                    name TEXT PRIMARY KEY,
                    layout_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pair_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            cur.execute("INSERT OR IGNORE INTO layout_state (id, revision, master_id, items_json, active_preset) VALUES (1, 1, NULL, '[]', NULL)")
            cur.execute("INSERT OR IGNORE INTO pair_state (id, active, started_at, expires_at) VALUES (1, 0, 0, 0)")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _next_code(self, cur: sqlite3.Cursor) -> str:
        cur.execute("SELECT id FROM nodes")
        used = {row["id"] for row in cur.fetchall()}
        idx = 1
        while f"P{idx}" in used:
            idx += 1
        return f"P{idx}"

    @staticmethod
    def _code_sort_key(node_id: str) -> tuple:
        if node_id.startswith("P") and node_id[1:].isdigit():
            return (0, int(node_id[1:]), node_id)
        return (1, 0, node_id)

    def _get_pair_locked(self, cur: sqlite3.Cursor, now: float) -> Dict[str, Any]:
        cur.execute("SELECT active, started_at, expires_at FROM pair_state WHERE id = 1")
        row = cur.fetchone()
        active = bool(row["active"])
        expires_at = float(row["expires_at"])
        if active and now >= expires_at:
            cur.execute("UPDATE pair_state SET active = 0 WHERE id = 1")
            self._conn.commit()
            active = False
        remaining = max(0.0, expires_at - now) if active else 0.0
        return {"active": active, "remaining": round(remaining, 1)}

    def _nodes_map_locked(self, cur: sqlite3.Cursor, now: float) -> Dict[str, Dict[str, Any]]:
        cur.execute("SELECT * FROM nodes")
        out: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            item = dict(row)
            item["monitors"] = json.loads(item.pop("monitors_json") or "[]")
            item["deskflow_installed"] = bool(item["deskflow_installed"])
            item["online"] = (now - float(item["last_seen"])) < 6.0
            out[item["id"]] = item
        return out

    def _layout_locked(self, cur: sqlite3.Cursor) -> Dict[str, Any]:
        cur.execute("SELECT revision, master_id, items_json, active_preset FROM layout_state WHERE id = 1")
        row = cur.fetchone()
        return {
            "revision": int(row["revision"]),
            "master_id": row["master_id"],
            "items": json.loads(row["items_json"] or "[]"),
            "active_preset": row["active_preset"],
        }

    def heartbeat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        machine_key = str(payload.get("machine_key") or payload.get("tailscale_ip") or payload.get("hostname") or "").strip()
        if not machine_key:
            raise ValueError("Missing machine_key")

        computer_name = str(payload.get("computer_name") or payload.get("hostname") or "pc").strip()
        hostname = str(payload.get("hostname") or computer_name).strip()
        os_name = str(payload.get("os") or "unknown").strip()
        os_detail = str(payload.get("os_detail") or os_name).strip()
        tailscale_ip = str(payload.get("tailscale_ip") or "").strip()
        dns_name = str(payload.get("dns_name") or "").strip()
        display_w = max(1, int(payload.get("display_w") or 1920))
        display_h = max(1, int(payload.get("display_h") or 1080))
        monitors_json = json.dumps(payload.get("monitors") or [])
        df_installed = 1 if payload.get("deskflow_installed") else 0
        df_status = str(payload.get("deskflow_status") or "idle").strip()
        df_log = str(payload.get("deskflow_log") or "")
        server_conf_path = str(payload.get("server_conf_path") or "deskflow-server.conf").strip()

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT id FROM nodes WHERE machine_key = ?", (machine_key,))
            row = cur.fetchone()
            if row:
                node_id = row["id"]
                cur.execute(
                    """
                    UPDATE nodes
                    SET computer_name=?, hostname=?, os=?, os_detail=?, tailscale_ip=?, dns_name=?,
                        display_w=?, display_h=?, monitors_json=?, deskflow_installed=?,
                        deskflow_status=?, deskflow_log=?, server_conf_path=?, last_seen=?
                    WHERE id=?
                    """,
                    (
                        computer_name,
                        hostname,
                        os_name,
                        os_detail,
                        tailscale_ip,
                        dns_name,
                        display_w,
                        display_h,
                        monitors_json,
                        df_installed,
                        df_status,
                        df_log,
                        server_conf_path,
                        now,
                        node_id,
                    ),
                )
            else:
                node_id = self._next_code(cur)
                cur.execute(
                    """
                    INSERT INTO nodes (
                        id, machine_key, computer_name, hostname, os, os_detail,
                        tailscale_ip, dns_name, display_w, display_h, monitors_json,
                        deskflow_installed, deskflow_status, deskflow_log, server_conf_path, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        machine_key,
                        computer_name,
                        hostname,
                        os_name,
                        os_detail,
                        tailscale_ip,
                        dns_name,
                        display_w,
                        display_h,
                        monitors_json,
                        df_installed,
                        df_status,
                        df_log,
                        server_conf_path,
                        now,
                    ),
                )
            self._conn.commit()

            pair = self._get_pair_locked(cur, now)
            layout = self._layout_locked(cur)
            nodes_map = self._nodes_map_locked(cur, now)

        node = nodes_map[node_id]
        master_id = layout["master_id"]
        in_layout = any(str(it.get("id")) == node_id for it in layout["items"])

        desired_role = "idle"
        deskflow_ini = ""
        server_conf = ""
        if master_id and in_layout and master_id in nodes_map:
            master_node = nodes_map[master_id]
            if node_id == master_id:
                desired_role = "server"
                server_conf = layout_compiler.compile_server_conf(nodes_map, layout)
                deskflow_ini = layout_compiler.compile_deskflow_ini(
                    computer_name=node["computer_name"],
                    role="server",
                    local_ts_ip=node["tailscale_ip"],
                    master_ts_ip=master_node["tailscale_ip"],
                    server_conf_path=node["server_conf_path"],
                )
            elif master_node.get("tailscale_ip"):
                desired_role = "client"
                deskflow_ini = layout_compiler.compile_deskflow_ini(
                    computer_name=node["computer_name"],
                    role="client",
                    local_ts_ip=node["tailscale_ip"],
                    master_ts_ip=master_node["tailscale_ip"],
                    server_conf_path=node["server_conf_path"],
                )

        return {
            "code": node_id,
            "pair_active": pair["active"],
            "pair_expires_in": pair["remaining"],
            "revision": layout["revision"],
            "desired_role": desired_role,
            "master_id": master_id,
            "deskflow_ini": deskflow_ini,
            "server_conf": server_conf,
        }

    def get_state(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            pair = self._get_pair_locked(cur, now)
            layout = self._layout_locked(cur)
            nodes_map = self._nodes_map_locked(cur, now)
            cur.execute("SELECT name, layout_json, updated_at FROM presets ORDER BY updated_at DESC")
            presets = [
                {
                    "name": row["name"],
                    "layout": json.loads(row["layout_json"]),
                    "updated_at": float(row["updated_at"]),
                }
                for row in cur.fetchall()
            ]

        nodes_list = sorted(nodes_map.values(), key=lambda n: self._code_sort_key(n["id"]))
        names_by_id = {n["id"]: n["computer_name"] for n in nodes_list}
        links = layout_compiler.compute_links(layout["items"], names_by_id)
        server_conf_preview = layout_compiler.compile_server_conf(nodes_map, layout) if layout["items"] else ""
        return {
            "now": now,
            "revision": layout["revision"],
            "pair_active": pair["active"],
            "pair_expires_in": pair["remaining"],
            "nodes": nodes_list,
            "layout": {
                "master_id": layout["master_id"],
                "items": layout["items"],
                "active_preset": layout["active_preset"],
            },
            "links": links,
            "server_conf_preview": server_conf_preview,
            "presets": presets,
        }

    def start_pair(self, duration: float = 45.0) -> Dict[str, Any]:
        now = time.time()
        dur = max(5.0, min(300.0, float(duration)))
        with self._lock:
            self._conn.execute(
                "UPDATE pair_state SET active = 1, started_at = ?, expires_at = ? WHERE id = 1",
                (now, now + dur),
            )
            self._conn.commit()
        return {"pair_active": True, "pair_expires_in": dur}

    def stop_pair(self) -> Dict[str, Any]:
        with self._lock:
            self._conn.execute("UPDATE pair_state SET active = 0, expires_at = 0 WHERE id = 1")
            self._conn.commit()
        return {"pair_active": False, "pair_expires_in": 0}

    def apply_layout(
        self,
        master_id: Optional[str],
        items: List[Dict[str, Any]],
        active_preset: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            nodes_map = self._nodes_map_locked(cur, now)
            cleaned: List[Dict[str, Any]] = []
            seen = set()
            for it in items:
                nid = layout_compiler.normalize_code(str(it.get("id") or ""))
                if nid not in nodes_map or nid in seen:
                    continue
                seen.add(nid)
                ar = layout_compiler.node_aspect_ratio(nodes_map[nid])
                h = round(max(0.5, float(it.get("h") or 1.0)), 3)
                w = round(max(0.5, float(it.get("w") or (h * ar))), 3)
                cleaned.append(
                    {
                        "id": nid,
                        "x": round(float(it.get("x") or 0.0), 3),
                        "y": round(float(it.get("y") or 0.0), 3),
                        "w": w,
                        "h": h,
                    }
                )
            norm_master = layout_compiler.normalize_code(master_id) if master_id else None
            if cleaned and (not norm_master or norm_master not in seen):
                norm_master = cleaned[0]["id"]
            if not cleaned:
                norm_master = None

            cur.execute("SELECT revision FROM layout_state WHERE id = 1")
            rev = int(cur.fetchone()["revision"]) + 1
            cur.execute(
                "UPDATE layout_state SET revision=?, master_id=?, items_json=?, active_preset=? WHERE id=1",
                (rev, norm_master, json.dumps(cleaned), active_preset),
            )
            cur.execute("UPDATE pair_state SET active = 0, expires_at = 0 WHERE id = 1")
            self._conn.commit()
            preview = layout_compiler.compile_server_conf(
                nodes_map, {"master_id": norm_master, "items": cleaned}
            )
        return {
            "revision": rev,
            "master_id": norm_master,
            "items": cleaned,
            "server_conf_preview": preview,
        }

    def stop_layout(self) -> Dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT revision, items_json FROM layout_state WHERE id = 1")
            row = cur.fetchone()
            rev = int(row["revision"]) + 1
            cur.execute(
                "UPDATE layout_state SET revision=?, master_id=NULL, active_preset=NULL WHERE id=1",
                (rev,),
            )
            self._conn.commit()
        return {"revision": rev, "master_id": None}

    def solve_from_photo(self, detections: List[Dict[str, Any]], apply_now: bool = True) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            nodes_map = self._nodes_map_locked(cur, now)
            layout = self._layout_locked(cur)
        solved = layout_compiler.solve_layout_from_photo(
            detections=detections,
            nodes_by_code=nodes_map,
            existing_master_id=layout.get("master_id"),
        )
        if apply_now and solved["items"]:
            res = self.apply_layout(solved["master_id"], solved["items"], active_preset=None)
            solved["revision"] = res["revision"]
            solved["applied"] = True
        else:
            solved["applied"] = False
        return solved

    def save_preset(self, name: str, layout: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Preset name required")
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            if layout is None:
                cur_layout = self._layout_locked(cur)
                data = {"master_id": cur_layout["master_id"], "items": cur_layout["items"]}
            else:
                data = {"master_id": layout.get("master_id"), "items": layout.get("items") or []}
            cur.execute(
                "INSERT INTO presets (name, layout_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET layout_json=excluded.layout_json, updated_at=excluded.updated_at",
                (clean_name, json.dumps(data), now),
            )
            cur.execute("UPDATE layout_state SET active_preset=? WHERE id=1", (clean_name,))
            self._conn.commit()
        return {"name": clean_name, "layout": data, "updated_at": now}

    def apply_preset(self, name: str) -> Dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT layout_json FROM presets WHERE name = ?", (name.strip(),))
            row = cur.fetchone()
            if not row:
                raise KeyError(name)
            data = json.loads(row["layout_json"])
        return self.apply_layout(data.get("master_id"), data.get("items") or [], active_preset=name.strip())

    def delete_preset(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM presets WHERE name = ?", (name.strip(),))
            self._conn.commit()

    def delete_node(self, node_id: str) -> None:
        nid = layout_compiler.normalize_code(node_id)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM nodes WHERE id = ?", (nid,))
            layout = self._layout_locked(cur)
            new_items = [it for it in layout["items"] if str(it.get("id")) != nid]
            if len(new_items) != len(layout["items"]):
                new_master = layout["master_id"] if layout["master_id"] != nid else (new_items[0]["id"] if new_items else None)
                cur.execute(
                    "UPDATE layout_state SET revision=?, master_id=?, items_json=? WHERE id=1",
                    (layout["revision"] + 1, new_master, json.dumps(new_items)),
                )
            self._conn.commit()


def create_server(host: str, port: int, db_path: Path, web_index: Path = _WEB_INDEX) -> ThreadingHTTPServer:
    store = FleetStore(db_path)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet stdout logs
            return

        def _send_bytes(self, status: int, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, obj: Any) -> None:
            self._send_bytes(status, "application/json; charset=utf-8", json.dumps(obj).encode("utf-8"))

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                if web_index.exists():
                    self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", web_index.read_bytes())
                else:
                    self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain", b"web/index.html missing")
                return

            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            if path == "/api/state":
                state = store.get_state()
                ts_info = sysinfo.get_tailscale_info()
                state["hub"] = {
                    "tailscale_ip": ts_info.get("ip", ""),
                    "dns_name": ts_info.get("dns_name", ""),
                    "host_header": self.headers.get("Host", ""),
                }
                self._send_json(HTTPStatus.OK, state)
                return

            if path.startswith("/api/qr/") and path.endswith(".png"):
                code = path[len("/api/qr/") : -len(".png")]
                qs = parse_qs(parsed.query)
                payload_text = (qs.get("text") or [code])[0]
                scale = max(4, min(24, int((qs.get("scale") or ["12"])[0])))
                try:
                    mat = qr_gen.encode_qr(payload_text)
                    png = qr_gen.render_png(mat, scale=scale, border=4)
                    self._send_bytes(HTTPStatus.OK, "image/png", png)
                except Exception as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc}"})
                return

            try:
                if path == "/api/heartbeat":
                    self._send_json(HTTPStatus.OK, store.heartbeat(body))
                elif path == "/api/pair/start":
                    dur = float(body.get("duration") or 45.0)
                    self._send_json(HTTPStatus.OK, store.start_pair(dur))
                elif path == "/api/pair/stop":
                    self._send_json(HTTPStatus.OK, store.stop_pair())
                elif path == "/api/layout/apply":
                    res = store.apply_layout(
                        master_id=body.get("master_id"),
                        items=body.get("items") or [],
                        active_preset=body.get("active_preset"),
                    )
                    self._send_json(HTTPStatus.OK, res)
                elif path == "/api/layout/from-photo":
                    res = store.solve_from_photo(
                        detections=body.get("detections") or [],
                        apply_now=bool(body.get("apply", True)),
                    )
                    self._send_json(HTTPStatus.OK, res)
                elif path == "/api/layout/stop":
                    self._send_json(HTTPStatus.OK, store.stop_layout())
                elif path == "/api/presets/save":
                    res = store.save_preset(str(body.get("name") or ""), body.get("layout"))
                    self._send_json(HTTPStatus.OK, res)
                elif path == "/api/presets/apply":
                    res = store.apply_preset(str(body.get("name") or ""))
                    self._send_json(HTTPStatus.OK, res)
                elif path == "/api/nodes/delete":
                    store.delete_node(str(body.get("id") or ""))
                    self._send_json(HTTPStatus.OK, {"ok": True})
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {exc}"})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/presets/"):
                name = path[len("/api/presets/") :]
                from urllib.parse import unquote

                store.delete_preset(unquote(name))
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if path.startswith("/api/nodes/"):
                nid = path[len("/api/nodes/") :]
                store.delete_node(nid)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.store = store  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Deskflow-Tailscale Fleet Hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=24808)
    parser.add_argument("--db", default=str(default_data_dir() / "fleet.db"))
    args = parser.parse_args()

    srv = create_server(args.host, args.port, Path(args.db))
    ts = sysinfo.get_tailscale_info()
    print(f"Deskflow Fleet Hub listening on http://{args.host}:{args.port}")
    if ts.get("ip"):
        print(f"Tailscale URL: http://{ts['ip']}:{args.port}")
    if ts.get("dns_name"):
        print(f"MagicDNS URL:  http://{ts['dns_name']}:{args.port} (or HTTPS via tailscale serve)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        srv.store.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
