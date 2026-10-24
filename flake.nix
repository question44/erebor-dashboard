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
