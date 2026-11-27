{
  description = "Configurable Angular dashboard and optional local FastAPI controls";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; }; in rec {
          dashboard = pkgs.callPackage ./nix/package.nix { };
          control = pkgs.callPackage ./nix/control-package.nix { };
          default = dashboard;
        });
      checks = forAllSystems (system: let pkgs = import nixpkgs { inherit system; }; in {
        inherit (self.packages.${system}) dashboard control;
        # The module evaluates, and its option type follows the action catalog.
        module = let
          system' = nixpkgs.lib.nixosSystem {
            inherit system;
            modules = [
              self.nixosModules.default
              {
                boot.loader.grub.enable = false;
                fileSystems."/".device = "none";
                system.stateVersion = "26.05";
                services.erebor-dashboard = {
                  enable = true;
                  package = pkgs.emptyDirectory;
                  control = {
                    enable = true;
                    user = "alice";
                    enabledActions = [ "display-on" "reboot-windows" "moonshine-pair" ];
                  };
                };
              }
            ];
          };
          env = system'.config.systemd.user.services.dashboard-control.environment;
        in pkgs.writeText "module-eval.json" (builtins.toJSON {
          inherit (env) DASHBOARD_ACTIONS DASHBOARD_PORT DASHBOARD_LOG_LEVEL DASHBOARD_SCREEN_MODES
            DASHBOARD_IGNORED_OUTPUTS DASHBOARD_HYPERHDR;
        });
        nanokvm-wake = pkgs.runCommand "nanokvm-wake-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          cp -r ${./nanokvm} source
          chmod -R u+w source
          python3 -m unittest discover -s source -v
          touch "$out"
        '';
      });
      overlays.default = final: _: {
        erebor-dashboard = final.callPackage ./nix/package.nix { };
        erebor-dashboard-control = final.callPackage ./nix/control-package.nix { };
      };
      nixosModules.default = import ./nix/module.nix;
    };
}
