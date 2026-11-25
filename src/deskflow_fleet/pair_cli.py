"""Tiny CLI helper to trigger pairing, show the Web UI QR, or switch presets/master."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import tinyqr


def _default_hub_url() -> str:
    if os.name == "nt":
        cfg = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "deskflow-tailscale" / "config.json"
    else:
        cfg = Path.home() / ".config" / "deskflow-tailscale" / "config.json"
    if cfg.exists():
        try:
            val = json.loads(cfg.read_text(encoding="utf-8")).get("hub_url")
            if val:
                return str(val).rstrip("/")
        except Exception:
            pass
    return "http://127.0.0.1:24808"


def _req(url: str, method: str = "GET", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=4.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deskflow-Tailscale Pair CLI")
    parser.add_argument("--hub", default=_default_hub_url(), help="Hub URL (e.g. http://100.x.x.x:24808)")
    parser.add_argument("--duration", type=int, default=45, help="QR overlay duration in seconds")
    parser.add_argument("--preset", default="", help="Apply a saved preset immediately")
    parser.add_argument("--master", default="", help="Switch Master PC (e.g. P1)")
    parser.add_argument("--stop", action="store_true", help="Dismiss all QR overlays")
    args = parser.parse_args()

    hub = args.hub.rstrip("/")
    if args.stop:
        _req(f"{hub}/api/pair/stop", "POST", {})
        print("Pairing stopped.")
        return
    if args.preset:
        res = _req(f"{hub}/api/presets/apply", "POST", {"name": args.preset})
        print(f"Applied preset '{args.preset}' (Master: {res.get('master_id')}, rev {res.get('revision')}).")
        return
    if args.master:
        state = _req(f"{hub}/api/state")
        layout = state.get("layout") or {}
        res = _req(
            f"{hub}/api/layout/apply",
            "POST",
            {"master_id": args.master, "items": layout.get("items") or []},
        )
        print(f"Switched Master to {res.get('master_id')} (rev {res.get('revision')}).")
        return

    _req(f"{hub}/api/pair/start", "POST", {"duration": args.duration})
    state = _req(f"{hub}/api/state")
    hub_info = state.get("hub") or {}
    ts_ip = hub_info.get("tailscale_ip") or ""
    web_url = f"http://{ts_ip}:24808" if ts_ip else hub
    print(f"\nPairing overlays active for {args.duration}s. Online PCs:")
    for n in state.get("nodes") or []:
        dot = "ONLINE" if n.get("online") else "OFFLINE"
        df = "deskflow-ok" if n.get("deskflow_installed") else "NO-DESKFLOW"
        print(f"  [{n['id']}] ({dot}) {n['computer_name']:<18} {n['os']:<8} {n['display_w']}x{n['display_h']} ({df})")
    print(f"\nOpen Web UI on phone or PC: {web_url}")
    if hub_info.get("dns_name"):
        print(f"Tailscale Serve HTTPS URL: https://{hub_info['dns_name']}/")
    print("\nScan to open Web UI:")
    print(tinyqr.to_ascii(web_url))


if __name__ == "__main__":
    main()
