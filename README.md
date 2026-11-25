# deskflow-tailscale

Small, zero-bloat **Deskflow + Tailscale KVM Fleet** with single-photo multi-QR spatial desk scanning, live drag-and-drop 2D layout canvas, and 1-click Master switching & presets.

## Architecture

- **Fleet Hub (`src/hub.py`)**: Stdlib Python HTTP server + SQLite (`:24808`) serving the mobile-friendly Web UI and coordinating per-PC Deskflow configs over your private Tailscale tailnet.
- **Fleet Agent (`src/agent.py`)**: Cross-platform daemon (Windows + Linux X11/Wayland/Hyprland/NixOS) that reports screen resolution & Tailscale IP, renders temporary high-contrast QR overlays (`src/overlay.py`) during pairing, and runs `deskflow-core` (`server` or `client`) with `tlsEnabled=false` bound to your encrypted Tailscale WireGuard network.
- **Multi-QR Spatial Solver (`src/layout_compiler.py`)**: Detects `P1`, `P2`, ... screen QRs from a single photo (decoded in-browser via `zxing-wasm`) and compiles valid Deskflow v1.27.0 `Deskflow.conf` + `deskflow-server.conf` files.
- **Pair CLI (`src/pair_cli.py`)**: Triggers pairing mode and prints an ASCII QR code for the Web UI right in your terminal.
- **NixOS Module (`nix/deskflow-tailscale.nix`)**: Declarative NixOS configuration for `deskflow`, `xdg.portal`, Tailscale firewall ports, and `systemd.user.services.deskflow-fleet-agent`.

## Quick Setup

### 1. Run Hub + Agent on your primary Windows or Linux PC

Windows (PowerShell):
```powershell
git clone https://github.com/IAFahim/deskflow-tailscale.git "$env:LOCALAPPDATA\deskflow-tailscale\repo"
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\deskflow-tailscale\repo\scripts\install-windows.ps1" -Hub -HubUrl "http://127.0.0.1:24808"
```

Linux:
```bash
git clone https://github.com/IAFahim/deskflow-tailscale.git ~/.deskflow-tailscale
bash ~/.deskflow-tailscale/scripts/install-linux.sh --hub --hub-url "http://127.0.0.1:24808"
```

### 2. Join any extra PC (pointing to Hub's Tailscale IP)

Windows:
```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\deskflow-tailscale\repo\scripts\install-windows.ps1" -HubUrl "http://<HUB-TAILSCALE-IP>:24808"
```

Linux (Arch / Omarchy / Ubuntu / Fedora):
```bash
bash ~/.deskflow-tailscale/scripts/install-linux.sh --hub-url "http://<HUB-TAILSCALE-IP>:24808"
```

## Pairing Flow

1. Run `python src/pair_cli.py` (or tap **Pair / Show QRs** in the Web UI at `http://<HUB-TAILSCALE-IP>:24808`).
2. Each online PC shows a crisp `P1`, `P2`, ... QR code on screen.
3. Open the Web UI on your phone, tap **Snap / Upload Desk Photo**, take **one photo** showing your screens, and it automatically computes the spatial layout, assigns Master, connects all PCs, and dismisses the overlays.
4. Drag screens anytime on the 2D canvas, click **Set Master** on any PC to switch which keyboard/mouse controls the desk, or save presets (e.g. `4-PC Battlestation`, `2-PC Travel Desk`).

## Run Tests

```bash
python -m unittest discover -s tests -v
```
