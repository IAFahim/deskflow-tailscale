"""Tests for deskflow_fleet Hub API, Deskflow v1.27.0 config wiring, and Pair CLI."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deskflow_fleet import hub  # noqa: E402


class HubAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.srv = hub.create_server("127.0.0.1", 0, self.db_path)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.srv.store.close()  # type: ignore[attr-defined]
        self.tmp.cleanup()

    def _req(self, path: str, method: str = "GET", body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    def test_index_and_heartbeat_pair_photo_presets(self) -> None:
        # Web UI HTML is bundled inside the package and served at /
        st_idx, ct_idx, raw_idx = self._req("/")
        self.assertEqual(st_idx, 200)
        self.assertIn("text/html", ct_idx)
        self.assertIn(b"Deskflow", raw_idx)

        # Two PCs register via heartbeat
        _, _, raw1 = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.1",
                "computer_name": "main-pc",
                "hostname": "main-pc",
                "os": "windows",
                "tailscale_ip": "100.64.0.1",
                "display_w": 3840,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "C:/run/deskflow-server.conf",
            },
        )
        hb1 = json.loads(raw1)
        self.assertEqual(hb1["code"], "P1")
        self.assertEqual(hb1["desired_role"], "idle")

        _, _, raw2 = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.2",
                "computer_name": "workstation",
                "hostname": "workstation",
                "os": "linux",
                "tailscale_ip": "100.64.0.2",
                "display_w": 1920,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "/home/user/.local/share/deskflow-tailscale/run/deskflow-server.conf",
            },
        )
        hb2 = json.loads(raw2)
        self.assertEqual(hb2["code"], "P2")

        # Pair start + PNG QR endpoint
        self._req("/api/pair/start", "POST", {"duration": 30})
        st, ct, png = self._req("/api/qr/P1.png")
        self.assertEqual(st, 200)
        self.assertEqual(ct, "image/png")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

        # Solve layout from photo and auto-apply
        _, _, solved_raw = self._req(
            "/api/layout/from-photo",
            "POST",
            {
                "detections": [
                    {"code": "P1", "cx": 300, "cy": 500, "size": 100},
                    {"code": "P2", "cx": 1100, "cy": 500, "size": 100},
                ],
                "apply": True,
            },
        )
        solved = json.loads(solved_raw)
        self.assertTrue(solved["applied"])
        self.assertEqual(solved["master_id"], "P1")

        # Heartbeats now get server and client configs compatible with Deskflow v1.27.0
        _, _, hbi_after = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.1",
                "computer_name": "main-pc",
                "hostname": "main-pc",
                "os": "windows",
                "tailscale_ip": "100.64.0.1",
                "display_w": 3840,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "C:/run/deskflow-server.conf",
            },
        )
        r1 = json.loads(hbi_after)
        self.assertEqual(r1["desired_role"], "server")
        self.assertFalse(r1["pair_active"])
        self.assertIn("right(0,100) = workstation(0,100)", r1["server_conf"])
        self.assertNotIn("section: aliases", r1["server_conf"])
        self.assertIn("computerName=main-pc", r1["deskflow_ini"])
        self.assertIn("[computer_2]\nname=workstation", r1["deskflow_ini"])

        _, _, hb2_after = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.2",
                "computer_name": "workstation",
                "hostname": "workstation",
                "os": "linux",
                "tailscale_ip": "100.64.0.2",
                "display_w": 1920,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "/home/user/.local/share/deskflow-tailscale/run/deskflow-server.conf",
            },
        )
        r2 = json.loads(hb2_after)
        self.assertEqual(r2["desired_role"], "client")
        self.assertIn("remoteHost=100.64.0.1", r2["deskflow_ini"])

        # Save, stop, re-apply preset, delete preset & node
        self._req("/api/presets/save", "POST", {"name": "DeskA"})
        _, _, state_raw = self._req("/api/state")
        state = json.loads(state_raw)
        self.assertEqual(len(state["presets"]), 1)

        self._req("/api/layout/stop", "POST", {})
        self._req("/api/presets/apply", "POST", {"name": "DeskA"})
        _, _, state_raw2 = self._req("/api/state")
        self.assertEqual(json.loads(state_raw2)["layout"]["master_id"], "P1")

        st_dp, _, _ = self._req("/api/presets/DeskA", "DELETE")
        self.assertEqual(st_dp, 200)
        st_dn, _, _ = self._req("/api/nodes/P2", "DELETE")
        self.assertEqual(st_dn, 200)


if __name__ == "__main__":
    unittest.main()
