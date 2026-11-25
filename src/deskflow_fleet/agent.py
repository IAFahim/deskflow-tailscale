"""Cross-platform per-PC Deskflow-Tailscale Fleet Agent."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

import desk_layout
import tailscale_host

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def default_config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "deskflow-tailscale" / "config.json"
    return Path.home() / ".config" / "deskflow-tailscale" / "config.json"


def default_runtime_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "deskflow-tailscale" / "run"
    return Path.home() / ".local" / "share" / "deskflow-tailscale" / "run"


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 3.5) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _terminate_proc(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3.0)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass


class FleetAgent:
    def __init__(self, hub_url: str, runtime_dir: Path, computer_name: str = "") -> None:
        self.hub_url = hub_url.rstrip("/")
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.computer_name_override = computer_name.strip()
        self.server_conf_path = self.runtime_dir / "deskflow-server.conf"
        self.deskflow_conf_path = self.runtime_dir / "Deskflow.conf"
        self.overlay_proc: Optional[subprocess.Popen] = None
        self.deskflow_proc: Optional[subprocess.Popen] = None
        self.active_signature: Optional[str] = None
        self.active_role: str = "idle"
        self.last_restart_attempt: float = 0.0
        self.logs: Deque[str] = deque(maxlen=40)
        self.log_lock = threading.Lock()
        self.running = True
        self.machine_key = self._load_or_create_machine_key()
        self.env = tailscale_host.discover_gui_env()

    def _load_or_create_machine_key(self) -> str:
        ts = tailscale_host.get_tailscale_info()
        if ts.get("ip"):
            return f"ts:{ts['ip']}"
        key_file = self.runtime_dir / "machine_key.txt"
        if key_file.exists():
            val = key_file.read_text(encoding="utf-8").strip()
            if val:
                return val
        val = f"uuid:{uuid.uuid4().hex}"
        key_file.write_text(val, encoding="utf-8")
        return val

    def _append_log(self, line: str) -> None:
        with self.log_lock:
            self.logs.append(line.rstrip())

    def _log_snapshot(self) -> str:
        with self.log_lock:
            return "\n".join(self.logs)

    def _pump_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self._append_log(line)
        except Exception:
            pass

    def _sync_overlay(self, active: bool, code: str, label: str, expires_in: float) -> None:
        if active:
            if self.overlay_proc is None or self.overlay_proc.poll() is not None:
                timeout = max(5, int(round(expires_in)) + 1)
                cmd = [
                    sys.executable,
                    "-m",
                    "qr_overlay",
                    "--code",
                    str(code),
                    "--label",
                    str(label),
                    "--timeout",
                    str(timeout),
                ]
                self.overlay_proc = subprocess.Popen(
                    cmd,
                    env=self.env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW,
                )
        else:
            if self.overlay_proc is not None:
                _terminate_proc(self.overlay_proc)
                self.overlay_proc = None

    def _sync_deskflow(self, reply: Dict[str, Any], deskflow_bin: Optional[str]) -> None:
        role = str(reply.get("desired_role") or "idle")
        ini_text = str(reply.get("deskflow_ini") or "")
        srv_text = str(reply.get("server_conf") or "")
        if role == "idle" or not ini_text:
            if self.deskflow_proc is not None:
                _terminate_proc(self.deskflow_proc)
                self.deskflow_proc = None
            self.active_signature = None
            self.active_role = "idle"
            return

        if not deskflow_bin:
            self.active_role = "missing_binary"
            return

        sig = hashlib.sha256(f"{role}\n{ini_text}\n{srv_text}".encode("utf-8")).hexdigest()
        proc_dead = self.deskflow_proc is None or self.deskflow_proc.poll() is not None
        need_start = (sig != self.active_signature) or (proc_dead and (time.time() - self.last_restart_attempt) >= 3.0)
        if not need_start:
            return

        _terminate_proc(self.deskflow_proc)
        self.last_restart_attempt = time.time()
        if role == "server" and srv_text:
            self.server_conf_path.write_text(srv_text, encoding="utf-8")
        self.deskflow_conf_path.write_text(ini_text, encoding="utf-8")
        with self.log_lock:
            self.logs.clear()
        self._append_log(f"[agent] starting deskflow-core ({role})")
        cmd = [deskflow_bin, role, "-s", str(self.deskflow_conf_path), "--new-instance"]
        self.deskflow_proc = subprocess.Popen(
            cmd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._pump_output, args=(self.deskflow_proc,), daemon=True).start()
        self.active_signature = sig
        self.active_role = role

    def _current_status(self, deskflow_bin: Optional[str]) -> str:
        if not deskflow_bin:
            return "missing_binary"
        if self.active_role == "idle":
            return "idle"
        if self.deskflow_proc is None:
            return "stopped"
        rc = self.deskflow_proc.poll()
        if rc is None:
            return f"running ({self.active_role})"
        return f"exited ({rc})"

    def run_once(self) -> None:
        self.env = tailscale_host.discover_gui_env()
        ts = tailscale_host.get_tailscale_info()
        os_info = tailscale_host.get_os_info()
        displays = tailscale_host.get_displays(self.env)
        deskflow_bin = tailscale_host.find_deskflow_core()
        if ts.get("ip"):
            self.machine_key = f"ts:{ts['ip']}"
        hostname = ts.get("hostname") or os_info["os"]
        computer_name = self.computer_name_override or hostname
        payload = {
            "machine_key": self.machine_key,
            "computer_name": computer_name,
            "hostname": hostname,
            "os": os_info["os"],
            "os_detail": os_info["os_detail"],
            "tailscale_ip": ts.get("ip") or "",
            "dns_name": ts.get("dns_name") or "",
            "display_w": displays["width"],
            "display_h": displays["height"],
            "monitors": displays["monitors"],
            "deskflow_installed": bool(deskflow_bin),
            "deskflow_status": self._current_status(deskflow_bin),
            "deskflow_log": self._log_snapshot(),
            "server_conf_path": desk_layout.to_forward_slash_path(str(self.server_conf_path)),
        }
        reply = _post_json(f"{self.hub_url}/api/heartbeat", payload, timeout=3.5)
        self._sync_overlay(
            active=bool(reply.get("pair_active")),
            code=str(reply.get("code") or "P?"),
            label=computer_name,
            expires_in=float(reply.get("pair_expires_in") or 45.0),
        )
        self._sync_deskflow(reply, deskflow_bin)

    def run_forever(self) -> None:
        def _stop(_sig: int, _frame: Any) -> None:
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _stop)

        try:
            while self.running:
                try:
                    self.run_once()
                except (urllib.error.URLError, TimeoutError, OSError):
                    if self.overlay_proc is not None:
                        _terminate_proc(self.overlay_proc)
                        self.overlay_proc = None
                except Exception as exc:
                    self._append_log(f"[agent error] {exc}")
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(0.1)
        finally:
            _terminate_proc(self.overlay_proc)
            _terminate_proc(self.deskflow_proc)


def _write_pid(pid_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deskflow-Tailscale Fleet Agent")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--computer-name", default="")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    cfg: Dict[str, Any] = {}
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    hub_url = (args.hub_url or cfg.get("hub_url") or "http://127.0.0.1:24808").strip()
    computer_name = (args.computer_name or cfg.get("computer_name") or "").strip()
    agent = FleetAgent(hub_url=hub_url, runtime_dir=default_runtime_dir(), computer_name=computer_name)
    _write_pid(agent.runtime_dir / "agent.pid")
    if args.once:
        agent.run_once()
        return
    agent.run_forever()


if __name__ == "__main__":
    main()
