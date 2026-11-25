#!/usr/bin/env bash
set -euo pipefail

HUB_URL="http://127.0.0.1:24808"
IS_HUB=0
NO_SYSTEMD=0
COMPUTER_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub-url) HUB_URL="${2%/}"; shift 2 ;;
    --hub) IS_HUB=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --computer-name) COMPUTER_NAME="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CFG_DIR="${HOME}/.config/deskflow-tailscale"
DATA_DIR="${HOME}/.local/share/deskflow-tailscale"
mkdir -p "$CFG_DIR" "$DATA_DIR"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Warning: tailscale not found. Install & log in to Tailscale first." >&2
fi

# Install Deskflow + Python GTK3 only if missing
need_pkg=0
command -v deskflow-core >/dev/null 2>&1 || need_pkg=1
command -v python3 >/dev/null 2>&1 || need_pkg=1
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import gi; gi.require_version('Gtk', '3.0')" 2>/dev/null || need_pkg=1
fi

if [[ "$need_pkg" -eq 1 ]]; then
  if [[ -f /etc/NIXOS ]]; then
    echo "NixOS detected: import nix/deskflow-tailscale.nix in your configuration.nix."
  elif sudo -n true 2>/dev/null || [[ -t 0 ]]; then
    if command -v pacman >/dev/null 2>&1; then
      echo "Installing deskflow + python-gobject + gtk3 via pacman..."
      sudo pacman -S --needed deskflow python python-gobject gtk3
    elif command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y deskflow python3 python3-gi gir1.2-gtk-3.0 || sudo apt-get install -y python3 python3-gi gir1.2-gtk-3.0
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y deskflow python3 python3-gobject gtk3
    else
      echo "Please install deskflow, python3, and GTK3 PyGObject manually." >&2
    fi
  else
    echo "Notice: non-interactive shell without passwordless sudo; skipping package install." >&2
    echo "Run once in a terminal: sudo pacman -S --needed deskflow python python-gobject gtk3" >&2
  fi
fi

# Write config.json
cat > "$CFG_DIR/config.json" <<EOF
{
  "hub_url": "$HUB_URL",
  "computer_name": "$COMPUTER_NAME"
}
EOF

PY_BIN="$(command -v python3)"

start_nohup() {
  local name="$1"
  shift
  local pid_file="$DATA_DIR/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    [[ -n "$old_pid" ]] && kill "$old_pid" 2>/dev/null || true
    rm -f "$pid_file"
  fi
  nohup "$@" > "$DATA_DIR/${name}.log" 2>&1 &
  echo $! > "$pid_file"
}

if [[ "$NO_SYSTEMD" -eq 0 ]] && command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "$UNIT_DIR"

  systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR HYPRLAND_INSTANCE_SIGNATURE DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true

  if [[ "$IS_HUB" -eq 1 ]]; then
    cat > "$UNIT_DIR/deskflow-fleet-hub.service" <<EOF
[Unit]
Description=Deskflow-Tailscale Fleet Hub
After=network-online.target

[Service]
ExecStart=$PY_BIN $REPO_ROOT/src/hub.py --host 0.0.0.0 --port 24808
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now deskflow-fleet-hub.service
  fi

  cat > "$UNIT_DIR/deskflow-fleet-agent.service" <<EOF
[Unit]
Description=Deskflow-Tailscale Fleet Agent
After=graphical-session.target network-online.target

[Service]
ExecStart=$PY_BIN $REPO_ROOT/src/agent.py --hub-url $HUB_URL
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now deskflow-fleet-agent.service
  systemctl --user restart deskflow-fleet-agent.service
else
  if [[ "$IS_HUB" -eq 1 ]]; then
    start_nohup "hub" "$PY_BIN" "$REPO_ROOT/src/hub.py" --host 0.0.0.0 --port 24808
  fi
  start_nohup "agent" "$PY_BIN" "$REPO_ROOT/src/agent.py" --hub-url "$HUB_URL"
fi

echo "Deskflow Fleet Agent ready -> $HUB_URL"
