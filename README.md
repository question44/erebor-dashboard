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

Keep the network restriction: it also protects the first-run setup page.
The API requires matching Host/Origin, JSON, and `X-Erebor-Control: 1` for
POST/PUT/DELETE, bounds bodies (256 bytes for actions, 4 KiB for accounts),
and disables caching.

### Users

Enabling the control API turns on accounts. The public `config.json` then
contains only title, description and `domainSuffix`; services, actions and NanoKVM
URLs are served per user by `GET /api/control/config` after sign-in.

- **First run:** with no accounts, the dashboard shows a setup wizard that creates
  the administrator. Setup is refused once any account exists.
- **Administrators** see everything configured and manage users from the account
  menu → *Manage users*. They cannot delete or demote themselves, so one
  administrator always remains.
- **Other users** get a set of modules (`host-status`, `outlet`, `quick-actions`,
  `services`), services (by `id`) and actions. Turning a module off hides all its
  items. Actions are enforced by the API (403 otherwise); hiding a service only
  removes its link and does not protect the service itself.
- Accounts and sessions are in `~/.local/state/erebor-dashboard/users.sqlite3`
  (systemd `StateDirectory`, mode 0600) for the control user. Passwords use scrypt.
  Sessions are 30-day sliding `HttpOnly; SameSite=Strict` cookies on `/api/control`,
  `Secure` behind HTTPS. Changing a password signs that user out elsewhere.
  Five failed sign-ins lock the account with growing delays (up to 15 minutes).
- **Recovery:** stop `dashboard-control` (user unit) and delete `users.sqlite3`
  to return to the setup wizard.
- The installed app remembers the last signed-in view, so Wake and the cached
  dashboard still work while the host sleeps. Signing out clears it.
Status, OpenAPI and Swagger UI are under `/api/control/`.

The standalone command accepts environment variables `DASHBOARD_HOSTS` and
`DASHBOARD_ACTIONS` (comma-separated), and `DASHBOARD_WLED_URL`.

## NanoKVM ATX wake

### Outlet energy readings

Optional `settings.outletUrl` adds a read-only power panel with live watts and
today/month energy in kWh. Point it at the NanoKVM helper's HTTPS `/outlet`
endpoint and set `outletEnabled: true` in the helper's private config. Install
`/opt/tapo-query/tapo-query` and its runtime credentials on NanoKVM first.
The endpoint uses the same owner-only Tailscale identity check and CORS origins
as wake/status. It runs only the fixed read-only binary, limits its runtime,
shares readings for four seconds, and returns only normalized telemetry.
Failures show unavailable rather than retaining a previous reading. The UI
refreshes every five seconds while visible and online, independently of host OS
or power state. Credentials never enter frontend configuration or responses.

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
uses tailscaled’s local Unix socket at `/var/run/tailscale/tailscaled.sock`
with permission to query/ping peers, avoiding CLI startup overhead. While the LED
reports awake, it verifies the pinned identity/address/OS and sends a fresh
TSMP ping to each configured peer, with bounded timeouts. Online flags and old
handshakes are not used as proof. Exactly one reply identifies the OS; no reply,
conflicting replies or unavailable identity data produce unknown. These are independent network
observations, not hardware attestation; reconfigure if an identity is replaced.
`GET /status` includes `operatingSystem: {state, detail}` (`nixos`, `windows`, or
`unknown`). The header shows e.g. `Awake · NixOS`; sleep/off hides the OS, and
failed requests clear prior readings. The same cached PWA works when Windows
is running; first load/install and configuration caching must happen on NixOS.
When the independent wake/status connection is configured, all host API actions
require positively detected awake NixOS. Windows, unknown, checking, unavailable,
sleep and off disable those actions, including confirmation and send handlers.
Wake up remains governed by the power LED. Deployments without that independent
connection retain their existing host API controls.
Service cards follow the same OS availability rule and lose their navigation
targets while disabled, including keyboard navigation.

For fast NixOS detection, the browser also requests `GET /api/control/os` in
parallel with NanoKVM. This network-only, no-store endpoint checks the host's
OS release without querying devices or sampling GPIO. A fresh NixOS reply
immediately shows `Awake · NixOS`, enables host controls/services and prevents
Wake. The tooltip identifies the direct API confirmation while the motherboard
LED check continues. Missing/failed/non-NixOS replies do not identify Windows;
NanoKVM remains the independent fallback. Direct confirmations clear on
offline/visibility changes and failed refreshes, and aborted replies are ignored.

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
