# Erebor dashboard

An Angular Material service dashboard with installable PWA/offline support and
an optional loopback FastAPI service for desktop controls. Extracted from
`question44/nixos-config` at `aaef646`.

## Build and use

Requires Nix with flakes enabled and Linux. The flake exports
`packages.<system>.default` / `dashboard` (static website directory), `control`
(the `erebor-dashboard-control` command), `overlays.default`, and
`nixosModules.default`. x86_64-linux is tested; aarch64-linux is declared but
has not been built on hardware.

```sh
# Private repository: authenticate to GitHub with SSH first.
nix build 'git+ssh://git@github.com/question44/erebor-dashboard#dashboard'
nix build 'git+ssh://git@github.com/question44/erebor-dashboard#control'
nix flake check
```

Serve the dashboard output directory at the root of an HTTP(S) website.
HTTPS or localhost is required for PWA installation and service workers.
The default dashboard has no service cards or controls. Configure it with
`packages.<system>.dashboard.override { settings = { ... }; }`, or use the module:

```nix
# flake inputs:
inputs.erebor-dashboard = {
  url = "git+ssh://git@github.com/question44/erebor-dashboard";
  inputs.nixpkgs.follows = "nixpkgs";
};

# NixOS module, with the flake input passed in scope:
{ config, pkgs, ... }: {
  imports = [ inputs.erebor-dashboard.nixosModules.default ];
  services.erebor-dashboard = {
    enable = true;
    settings = {
      title = "My server";
      description = "Home services";
      services = [{
        id = "media";
        label = "Media";
        description = "Media library";
        icon = "media";
        url = "https://media.example.org";
      }];
      actions = [ ];
    };
  };
  services.caddy = {
    enable = true;
    virtualHosts."dashboard.example.org".extraConfig = ''
      root * ${config.services.erebor-dashboard.package}
      file_server
    '';
  };
}
```

`settings` is public JSON baked into the output and offline cache. Never put
credentials in it. Fields are `title`, `description`, `services`, `actions`,
and `domainSuffix`. Cards accept `id`, `label`, `description`, `icon`, and `url`.
Alternatively supply `subdomain`, `port`, and optional `scheme` (default `http`):
when the current hostname ends with `domainSuffix`, the link becomes
`https://<subdomain>.<current-hostname>`; otherwise it uses the current hostname
and supplied port/scheme. Available icons: display, light, sleep, power, media,
cloud, sync, torrent, code, system, windows, arrow.

## Optional controls

Enable `services.erebor-dashboard.control.enable`, set `control.user` to the
desktop user, `control.allowedHosts` to dashboard hostnames, and
`control.enabledActions` to the explicitly permitted actions. Add the same action
IDs to `settings.actions` to display buttons. Disabled API actions return 403.
No actions are permitted by default, including when running the command directly.

Available actions: `display-on`, `display-off`, `ambilight-on`, `ambilight-off`,
`sleep`, `power-off`, `reboot-windows`. Supply compositor packages through
`control.extraPackages` for Niri, Umbriel, or Mango. Ambilight requires local
HyperHDR on port 19444 and `control.wledUrl`. `reboot-windows` calls
`dashboard-reboot-windows.service`; the host must provide its privileged helper
and authorization. This repository does not install boot-changing helpers or
Polkit grants. Sleep/power-off depend on the host's login1 authorization policy.

The module runs a user service on **127.0.0.1:18090**. A reverse proxy must route
`/api/control*` there and restrict access to trusted networks, for example inside
the Caddy virtual host before `file_server`:

```caddyfile
handle /api/control* {
  @trusted remote_ip private_ranges 100.64.0.0/10 fd7a:115c:a1e0::/48
  handle @trusted {
    reverse_proxy 127.0.0.1:18090
  }
  respond "Controls are available on trusted networks only." 403
}
handle {
  root * /path/to/dashboard-output
  file_server
}
```

Keep the network restriction: same-origin checks are a browser boundary, not
user authentication. The API requires matching Host/Origin, JSON, and
`X-Erebor-Control: 1` for POSTs, bounds bodies to 256 bytes, and disables caching.
Status, OpenAPI and Swagger UI are under `/api/control/`.

The standalone command accepts environment variables `DASHBOARD_HOSTS` and
`DASHBOARD_ACTIONS` (comma-separated), and `DASHBOARD_WLED_URL`.

## Development and submodule workflow

Frontend sources are in `frontend/`, API and mocked action tests in `api/`, and
Nix definitions in `nix/`. Run `npm ci && npm run build` inside `frontend/` for a
non-Nix frontend build. `nix flake check` builds the default frontend and runs
API tests without issuing system commands. Review package-lock changes and
update `npmDepsHash` when changing dependencies.

In the NixOS parent repository:

```sh
git clone --recurse-submodules git@github.com:question44/nixos-config.git
# In an existing checkout:
git submodule update --init --recursive
# Edit apps/erebor-dashboard, commit and push there first, then:
git add apps/erebor-dashboard
git commit -m "Update dashboard submodule"
```

The parent uses `inputs.self.submodules = true` (a recent Nix version is needed)
and a relative path flake input following its own nixpkgs. The Git submodule pins
the source revision; parent flake updates do not independently advance it.
