{ config, pkgs, lib, ... }:

let
  cfg = config.services.deskflowFleet;
in
{
  options.services.deskflowFleet = {
    enable = lib.mkEnableOption "Deskflow-Tailscale Fleet Agent";
    hubUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://127.0.0.1:24808";
      description = "URL of the Deskflow Fleet Hub over Tailscale";
    };
    venvDir = lib.mkOption {
      type = lib.types.str;
      default = "%h/.local/share/deskflow-tailscale/venv";
      description = "Path to the Python venv where deskflow-tailscale is installed";
    };
    hub = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Also open port 24808 on tailscale0 if this host runs the Hub";
    };
    deskflowPort = lib.mkOption {
      type = lib.types.port;
      default = 24800;
      description = "Deskflow TCP/UDP port on tailscale0";
    };
    useLanMouse = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Set true if running a minimal wlroots compositor without XDG InputCapture portals";
    };
  };

  config = lib.mkIf cfg.enable {
    services.tailscale.enable = true;

    environment.systemPackages = with pkgs; [
      (if cfg.useLanMouse then lan-mouse else deskflow)
      python3
      gtk3
      python3Packages.pygobject3
    ];

    xdg.portal = {
      enable = true;
      extraPortals = with pkgs; [
        xdg-desktop-portal-gtk
        xdg-desktop-portal-hyprland
      ];
      # For Hyprland Deskflow Client (RemoteDesktop + Clipboard portals), add
      # `github:waycrate/xdg-desktop-portal-luminous` to extraPortals and route:
      # config.hyprland."org.freedesktop.impl.portal.RemoteDesktop" = [ "luminous" ];
      # config.hyprland."org.freedesktop.impl.portal.Clipboard" = [ "luminous" ];
    };

    # Trust Deskflow only on the encrypted Tailscale WireGuard interface.
    networking.firewall.interfaces.tailscale0 = {
      allowedTCPPorts = [ cfg.deskflowPort ] ++ lib.optional cfg.hub 24808;
      allowedUDPPorts = [ cfg.deskflowPort ];
    };

    systemd.user.services.deskflow-fleet-agent = {
      description = "Deskflow-Tailscale Fleet Agent";
      wantedBy = [ "graphical-session.target" ];
      after = [ "graphical-session.target" "network-online.target" ];
      serviceConfig = {
        ExecStart = "${cfg.venvDir}/bin/python -m deskflow_fleet.agent --hub-url ${cfg.hubUrl}";
        Restart = "always";
        RestartSec = 3;
      };
    };
  };
}
