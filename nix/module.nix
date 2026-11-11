{ config, lib, pkgs, ... }:
let
  cfg = config.services.erebor-dashboard;
  settingsFormat = pkgs.formats.json { };
  actionType = lib.types.enum [ "display-on" "display-off" "screen-dp" "screen-hdmi" "ambilight-on" "ambilight-off"
    "profile-performance" "profile-balanced" "profile-power-saver" "sleep" "power-off" "reboot-windows" "moonshine-pair" ];
in
{
  options.services.erebor-dashboard = {
    enable = lib.mkEnableOption "the Erebor dashboard package";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { settings = cfg.settings; auth = cfg.control.enable; };
      description = ''
        Built static dashboard; serve this directory as the website root. With the control API
        enabled, sign-in is required and services/actions are served per user by the API.
      '';
    };
    settings = lib.mkOption {
      type = settingsFormat.type;
      default = { };
      description = "Public frontend configuration baked into the package. Do not put secrets here.";
    };
    control = {
      enable = lib.mkEnableOption "the loopback dashboard control API";
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ./control-package.nix { };
        description = "Dashboard control API package.";
      };
      user = lib.mkOption {
        type = lib.types.str;
        description = "Desktop user whose user manager runs the API.";
      };
      allowedHosts = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ "localhost" "127.0.0.1" ];
        description = "Hostnames accepted by the same-origin request boundary. Private and tailnet IPs are also accepted.";
      };
      enabledActions = lib.mkOption {
        type = lib.types.listOf actionType;
        default = [ ];
        description = "Actions the API may execute. Windows reboot needs a separately supplied privileged helper.";
      };
      wledUrl = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "WLED HTTP URL; HyperHDR is expected on loopback port 19444.";
      };
      moonshineUrl = lib.mkOption {
        type = lib.types.str;
        default = "http://127.0.0.1:47989";
        description = "Moonshine HTTP endpoint for `moonshine-pair`; pending client IDs come from moonshine.service's journal.";
      };
      tasks = {
        command = lib.mkOption {
          type = lib.types.nullOr (lib.types.listOf lib.types.str);
          default = null;
          example = [ "/etc/nixos/scripts/erebor-tasks" "--root" "/etc/nixos" "list" "--json" ];
          description = ''
            Command (argv) printing a task board as JSON; administrators then see a read-only
            task card. Runs on each refresh as the control user.
          '';
        };
        linkBase = lib.mkOption {
          type = lib.types.str;
          default = "";
          example = "https://github.com/owner/repo/blob/master/";
          description = "URL prefix for the board's repository-relative task paths; empty means no links.";
        };
      };
      sequoiaData = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        example = "/home/alice/.local/share/sequoia/web.json";
        description = ''
          Path (read at request time, not copied into the store) of the Sequoia wallpaper data
          for the "Sequoia" page background, served to signed-in users. Null hides it.
        '';
      };
      extraPackages = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [ ];
        description = "Compositor command packages used by display actions.";
      };
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = !cfg.control.enable || cfg.control.user != "";
      message = "services.erebor-dashboard.control.user must name a desktop user.";
    }];
    systemd.user.services.dashboard-control = lib.mkIf cfg.control.enable {
      description = "Local dashboard controls";
      wantedBy = [ "default.target" ];
      unitConfig.ConditionUser = cfg.control.user;
      environment = {
        DASHBOARD_HOSTS = lib.concatStringsSep "," cfg.control.allowedHosts;
        DASHBOARD_ACTIONS = lib.concatStringsSep "," cfg.control.enabledActions;
        DASHBOARD_MOONSHINE_URL = cfg.control.moonshineUrl;
        DASHBOARD_SETTINGS = settingsFormat.generate "erebor-dashboard-settings.json" cfg.settings;
      } // lib.optionalAttrs (cfg.control.tasks.command != null) {
        DASHBOARD_TASKS_COMMAND = builtins.toJSON cfg.control.tasks.command;
        DASHBOARD_TASKS_LINK_BASE = cfg.control.tasks.linkBase;
      } // lib.optionalAttrs (cfg.control.sequoiaData != null) {
        DASHBOARD_SEQUOIA_DATA = cfg.control.sequoiaData;
      } // lib.optionalAttrs (cfg.control.wledUrl != null) {
        DASHBOARD_WLED_URL = cfg.control.wledUrl;
      };
      path = [ pkgs.systemd pkgs.wlopm ] ++ cfg.control.extraPackages;
      serviceConfig = {
        ExecStart = lib.getExe cfg.control.package;
        # Accounts and sessions: ~/.local/state/erebor-dashboard/users.sqlite3
        StateDirectory = "erebor-dashboard";
        StateDirectoryMode = "0700";
        Restart = "on-failure";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
        LockPersonality = true;
        UMask = "0077";
      };
    };
  };
}
