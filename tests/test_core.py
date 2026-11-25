"""Automated unit tests for QR generator, layout compiler, photo solver, and Hub API."""

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

import hub  # noqa: E402
import layout_compiler  # noqa: E402
import qr_gen  # noqa: E402


class QRGenTests(unittest.TestCase):
    def test_qr_matrix_dimensions_and_finders(self) -> None:
        m = qr_gen.encode_qr("P1")
        self.assertEqual(len(m), 21)
        self.assertEqual(len(m[0]), 21)
        # Top-left 7x7 finder outer ring
        self.assertEqual(m[0][:7], [1, 1, 1, 1, 1, 1, 1])
        self.assertEqual(m[1][:7], [1, 0, 0, 0, 0, 0, 1])
        self.assertEqual(m[3][2:5], [1, 1, 1])
        # Dark module at (4*1+9, 8) = (13, 8)
        self.assertEqual(m[13][8], 1)

    def test_qr_url_versions_and_png(self) -> None:
        for txt in ("P2", "http://100.64.0.10:24808", "https://hub.example.ts.net/"):
            mat = qr_gen.encode_qr(txt)
            self.assertEqual(len(mat), len(mat[0]))
            png = qr_gen.render_png(mat, scale=8, border=2)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(png), 150)

    def test_rs_syndrome_zero(self) -> None:
        codewords = qr_gen._encode_codewords(b"P1", 1, "H")
        self.assertEqual(len(codewords), 26)
        self.assertTrue(qr_gen.rs_verify(codewords, 17))


class LayoutCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = {
            "P1": {"id": "P1", "computer_name": "admin", "tailscale_ip": "100.64.0.1", "display_w": 3840, "display_h": 1080},
            "P2": {"id": "P2", "computer_name": "os", "tailscale_ip": "100.64.0.2", "display_w": 3840, "display_h": 1080},
            "P3": {"id": "P3", "computer_name": "laptop", "tailscale_ip": "100.64.0.3", "display_w": 1920, "display_h": 1080},
            "P4": {"id": "P4", "computer_name": "box4", "tailscale_ip": "100.64.0.4", "display_w": 1920, "display_h": 1080},
        }

    def test_two_pc_side_by_side_and_ini_forward_slashes(self) -> None:
        layout = {
            "master_id": "P1",
            "items": [
                {"id": "P1", "x": 0.0, "y": 0.0, "w": 3.556, "h": 1.0},
                {"id": "P2", "x": 3.556, "y": 0.0, "w": 3.556, "h": 1.0},
            ],
        }
        srv = layout_compiler.compile_server_conf(self.nodes, layout)
        self.assertIn("section: screens", srv)
        self.assertIn("right(0,100) = os(0,100)", srv)
        self.assertIn("left(0,100) = admin(0,100)", srv)
        self.assertNotIn("section: aliases", srv)

        ini = layout_compiler.compile_deskflow_ini(
            computer_name="admin",
            role="server",
            local_ts_ip="100.64.0.1",
            master_ts_ip="100.64.0.1",
            server_conf_path=r"C:\Users\It\AppData\Local\deskflow-tailscale\run\deskflow-server.conf",
        )
        self.assertIn("externalConfigFile=C:/Users/It/AppData/Local/deskflow-tailscale/run/deskflow-server.conf", ini)
        self.assertIn("tlsEnabled=false", ini)
        self.assertIn("interface=100.64.0.1", ini)

    def test_l_shape_no_overlapping_intervals(self) -> None:
        # P1 on left (h=1.0), P2 top-right (h=0.5), P3 bottom-right (h=0.5)
        layout = {
            "master_id": "P1",
            "items": [
                {"id": "P1", "x": 0.0, "y": 0.0, "w": 1.778, "h": 1.0},
                {"id": "P2", "x": 1.778, "y": 0.0, "w": 1.778, "h": 0.5},
                {"id": "P3", "x": 1.778, "y": 0.5, "w": 1.778, "h": 0.5},
            ],
        }
        links = layout_compiler.compute_links(
            layout["items"],
            {"P1": "admin", "P2": "os", "P3": "laptop"},
        )
        p1_rights = [lk for lk in links["P1"] if lk["dir"] == "right"]
        self.assertEqual(len(p1_rights), 2)
        p1_rights.sort(key=lambda lk: lk["s0"])
        self.assertLessEqual(p1_rights[0]["s1"], p1_rights[1]["s0"])

    def test_solve_from_photo_row_and_grid(self) -> None:
        # 2 side-by-side PCs
        det2 = [
            {"code": "P1", "cx": 400, "cy": 500, "size": 120},
            {"code": "P2", "cx": 1100, "cy": 510, "size": 120},
        ]
        res2 = layout_compiler.solve_layout_from_photo(det2, self.nodes)
        self.assertEqual(len(res2["items"]), 2)
        a, b = res2["items"]
        self.assertAlmostEqual(a["y"], b["y"], places=2)
        self.assertGreaterEqual(b["x"], a["x"] + a["w"] - 1e-3)

        # 4 PCs in 2x2 grid
        det4 = [
            {"code": "P1", "cx": 400, "cy": 300, "size": 100},
            {"code": "P2", "cx": 1100, "cy": 310, "size": 100},
            {"code": "P3", "cx": 420, "cy": 800, "size": 100},
            {"code": "P4", "cx": 1120, "cy": 790, "size": 100},
        ]
        res4 = layout_compiler.solve_layout_from_photo(det4, self.nodes)
        self.assertEqual(len(res4["items"]), 4)
        # Assert zero pairwise overlaps
        items = res4["items"]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                x, y = items[i], items[j]
                ox = min(x["x"] + x["w"], y["x"] + y["w"]) - max(x["x"], y["x"])
                oy = min(x["y"] + x["h"], y["y"] + y["h"]) - max(x["y"], y["y"])
                self.assertFalse(ox > 1e-2 and oy > 1e-2, f"Overlap between {x['id']} and {y['id']}")


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

    def test_heartbeat_pair_photo_and_presets(self) -> None:
        _, _, raw1 = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.1",
                "computer_name": "admin",
                "hostname": "admin",
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
                "computer_name": "os",
                "hostname": "os",
                "os": "linux",
                "tailscale_ip": "100.64.0.2",
                "display_w": 3840,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "/home/os/.local/share/deskflow-tailscale/run/deskflow-server.conf",
            },
        )
        hb2 = json.loads(raw2)
        self.assertEqual(hb2["code"], "P2")

        # Pair start + PNG endpoint
        self._req("/api/pair/start", "POST", {"duration": 30})
        st, ct, png = self._req("/api/qr/P1.png")
        self.assertEqual(st, 200)
        self.assertEqual(ct, "image/png")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

        # Solve from photo + auto apply
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

        # Heartbeats now get server and client roles
        _, _, hb1_after = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.1",
                "computer_name": "admin",
                "hostname": "admin",
                "os": "windows",
                "tailscale_ip": "100.64.0.1",
                "display_w": 3840,
                "display_h": 1080,
                "deskflow_installed": True,
                "server_conf_path": "C:/run/deskflow-server.conf",
            },
        )
        r1 = json.loads(hb1_after)
        self.assertEqual(r1["desired_role"], "server")
        self.assertFalse(r1["pair_active"])
        self.assertIn("right(0,100) = os(0,100)", r1["server_conf"])

        _, _, hb2_after = self._req(
            "/api/heartbeat",
            "POST",
            {
                "machine_key": "ts:100.64.0.2",
                "computer_name": "os",
                "hostname": "os",
                "os": "linux",
                "tailscale_ip": "100.64.0.2",
                "display_w": 3840,
                "display_h": 1080,
                "deskflow_installed": True,
            },
        )
        r2 = json.loads(hb2_after)
        self.assertEqual(r2["desired_role"], "client")
        self.assertIn("remoteHost=100.64.0.1", r2["deskflow_ini"])

        # Save & apply preset switching master
        self._req("/api/presets/save", "POST", {"name": "DeskA"})
        _, _, state_raw = self._req("/api/state")
        state = json.loads(state_raw)
        self.assertEqual(len(state["presets"]), 1)
        self._req("/api/layout/stop", "POST", {})
        self._req("/api/presets/apply", "POST", {"name": "DeskA"})
        _, _, state_raw2 = self._req("/api/state")
        self.assertEqual(json.loads(state_raw2)["layout"]["master_id"], "P1")


if __name__ == "__main__":
    unittest.main()
