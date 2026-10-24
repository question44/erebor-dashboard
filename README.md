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

## NanoKVM ATX wake

Add `wake` to frontend `settings.actions` and set `settings.wakeUrl` to an
independent HTTPS endpoint, for example
`https://<nanokvm-magicdns-name>:8443/wake`. This action goes directly from the
browser to NanoKVM; do not add it to the sleeping host's `control.enabledActions`.
The cached PWA opens while the host sleeps and waits up to 90 seconds for its
control API after a wake request. A cached shell request falls back after 2.5s.

`nanokvm/wake.py` is a standard-library Python 3 service for NanoKVM Cube/PCIe
GPIO hardware. Install it outside `/kvmapp` as `/opt/erebor-wake/wake.py`, and put
a `config.json` next to it with `login` (the permitted Tailscale user's login)
and `origins` (an array of exact HTTPS dashboard origins). Install
`nanokvm/S99erebor-wake` at `/etc/init.d/S99erebor-wake`, mark it executable,
and run it with `start`. Then configure on NanoKVM:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:18091
```

Serve provides TLS and authenticated Tailscale identity headers; the helper
binds only to loopback and permits only the configured login. Browser requests
also require an allowed origin, JSON `{}`, and `X-Erebor-Wake: 1`. No NanoKVM
password or API token goes into frontend configuration. Tailscale must be on
on the client; the browser may request local network access permission. This
does not enable Funnel or expose the service publicly.

The only operation is a 200ms ATX press. The helper samples the active-low
power LED for four seconds: steady on refuses a press, off or blinking allows
one. Invalid GPIO readings fail closed; concurrent requests and repeats within
30 seconds are rejected. Wire both power switch and LED correctly. A sleeping
PC whose LED stays steadily on will be refused too. Test real wake behavior
on the target motherboard; software tests do not prove physical wiring or
the sleep LED pattern. Existing NanoKVM power controls can still operate the
same GPIO independently; avoid using them concurrently.

`GET /status` samples the LED without pressing the button and reports `awake`
(steady on), `asleep` (blinking), `off` (unlit), or `unknown` (unreadable).
The frontend polls this independently of the host API every 10 seconds, clears
stale readings on connection/visibility changes, and enables Wake up only when
the LED indicates asleep or off. Awake, unknown, checking and unavailable states
keep the button disabled. An unreachable NanoKVM is shown as unavailable, never
assumed asleep. LED states describe motherboard signals, not OS health.

Optionally add `osPeers` to the NanoKVM helper's private `config.json`:

```json
"osPeers": [
  {"state": "nixos", "nodeId": "<stable Tailscale node ID>", "ip": "100.x.y.z", "tailscaleOS": "linux"},
  {"state": "windows", "nodeId": "<native Windows node ID>", "ip": "100.a.b.c", "tailscaleOS": "windows"}
]
```

Get IDs, addresses and OS values from `tailscale status --json`. Configure only
the native installations on the host, excluding VMs. Each OS must run Tailscale
with a separate identity. No additional Windows service is needed. The helper
requires the `tailscale` CLI and permission to query/ping peers. While the LED
reports awake, it verifies the pinned identity/address/OS and sends a fresh
TSMP ping to each configured peer, with bounded timeouts. Online flags and old
handshakes are not used as proof. Exactly one reply identifies the OS; no reply,
conflicting replies or errors produce unknown. These are independent network
observations, not hardware attestation; reconfigure if an identity is replaced.
`GET /status` includes `operatingSystem: {state, detail}` (`nixos`, `windows`, or
`unknown`). The header shows e.g. `Awake · NixOS`; sleep/off hides the OS, and
failed requests clear prior readings. The same cached PWA works when Windows
is running; first load/install and configuration caching must happen on NixOS.

`GET /health` through Serve checks identity and service availability without
touching GPIO. Run `python3 -m unittest discover -s nanokvm -v` for mocked
GPIO and HTTP boundary tests (also included in flake checks). To disable:
`tailscale serve --https=8443 off`, stop the init script, and remove its execute
permission. NanoKVM reboots start the helper through the init script; Serve's
background configuration persists. Firmware updates may require redeployment.

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
