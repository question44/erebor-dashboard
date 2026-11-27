# Erebor dashboard

An Angular Material service dashboard with installable PWA/offline support and
an optional loopback FastAPI service for desktop controls. Public repository
`github:jmmzon/erebor-dashboard`; NixOS configurations consume it as a flake
input pinned to a revision. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Build and use

Requires Nix with flakes enabled and Linux. The flake exports
`packages.<system>.default` / `dashboard` (static website directory), `control`
(the `erebor-dashboard-control` command), `overlays.default`, and
`nixosModules.default`. x86_64-linux is tested; aarch64-linux is declared but
has not been built on hardware.

```sh
nix build github:jmmzon/erebor-dashboard#dashboard
nix build github:jmmzon/erebor-dashboard#control
nix flake check
```

Serve the dashboard output directory at the root of an HTTP(S) website.
HTTPS or localhost is required for PWA installation and service workers.
The default dashboard has no service cards or controls. Configure it with
`packages.<system>.dashboard.override { settings = { ... }; }`, or use the module:

```nix
# flake inputs:
inputs.erebor-dashboard = {
  url = "github:jmmzon/erebor-dashboard";
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

With accounts enabled, each user can tap the pencil (top right) to enter edit
mode: cards, action buttons and services wiggle, can be dragged into a new order
(arrow keys on the move button also work) and hidden with the eye button. Quick
actions form one grid (4 units, 2 on phones): status cards take 2 units, buttons 1,
and "Add spacer" inserts an empty 1-unit cell (also available among services).
Each section can be folded with the chevron in its heading to show only its first
N rows ("Show all" expands it). In edit mode a dashed fold line with a grip sets N:
drag it between rows (or use up/down arrows on the grip). Fold state and N are
stored per account. Clicking a section's heading row also folds/unfolds it; folding,
unfolding and reordering animate with a spring (disabled for reduced motion).

A service with `embedOn: ["dashboard.example.org"]` and an HTTPS `url` opens in
a draggable, resizable in-page window (full screen on phones) when the dashboard
is opened on one of those hostnames; elsewhere it stays a normal link. Each click
(or **+** in a window's title bar) opens another window, up to 8, each with its
own session; windows cascade, come forward when clicked, and can be minimised to
a dock at the bottom of the page. **Merge into tabs** (in any title bar) puts every window
into one tabbed window, and **Split into windows** reverses it; the terminals keep
running either way. The choice is remembered per device. The
service must allow framing, e.g. `frame-ancestors https://dashboard.example.org`. A **Show hidden**
toggle reveals hidden items again. The arrangement is stored per account in the
API's SQLite database (`PUT /api/control/layout`); without accounts it is kept
in the browser. The sign-in screen lists accounts recently used on that device.

## Optional controls

Enable `services.erebor-dashboard.control.enable`, set `control.user` to the
desktop user, `control.allowedHosts` to dashboard hostnames, and
`control.enabledActions` to the explicitly permitted actions. Add the same action
IDs to `settings.actions` to display buttons. Disabled API actions return 403.
No actions are permitted by default, including when running the command directly.

Every action is listed once in [`actions.json`](actions.json) (id, label, kind,
whether it asks for confirmation, and the endpoint the browser sends it to); the
API reads it at startup, the module derives `enabledActions`' type from it and
the frontend build imports it for button labels and confirmations.
API actions: `display-on`, `display-off`, `screen-dp`, `screen-hdmi`,
`ambilight-on`, `ambilight-off`, `profile-performance`, `profile-balanced`,
`profile-power-saver`, `sleep`, `power-off`, `reboot-windows`, `moonshine-pair`
(`wake` runs in the browser, see NanoKVM below).

- **Display and screen** actions go through a compositor adapter chosen by the
  session's `XDG_CURRENT_DESKTOP` (read from the user manager on each request).
  `api/control/compositors/` has a small interface (`outputs()`,
  `set_power(output, on)`, `screen_mode()`, `set_screen_mode(mode)`) and one
  adapter, Umbriel (`umbriel outputs --json`, `umbriel msg dpms-on|off`,
  `umbriel-display-mode`). Another compositor is one new module listed in
  `compositors.ADAPTERS`. Supply the compositor's commands through
  `control.extraPackages`. `control.ignoredOutputs` names virtual outputs
  (capture or streaming) that are never switched or counted;
  `control.screenModes` maps the screen switcher's modes to card labels
  (`GET /api/control/config` sends them as `screens` when a screen action is
  visible; the dashboard falls back to DP-1 and HDMI).
- **Profile** actions set power-profiles-daemon's `ActiveProfile` over D-Bus;
  grant the control user `org.freedesktop.UPower.PowerProfiles.switch-profile` in
  polkit, because the API runs outside an active session.
- **Ambilight** needs `control.wledUrl` and HyperHDR's JSON API at
  `control.hyperhdrAddress` (default `127.0.0.1:19444`).
- **Sleep/power-off** call login1 and depend on the host's authorization
  policy. **`reboot-windows`** starts `dashboard-reboot-windows.service`; the
  host must provide that privileged helper and its authorization. This
  repository does not install boot-changing helpers or polkit grants.
- **System control daemon:** with `control.systemDaemon` set to an erebor-sysd
  socket, the profile, sleep, power-off and Windows actions are sent to that
  daemon instead, which applies its policy and may ask the owner to approve
  (phone, security key, Mac). `GET /api/control/config` then lists the actions
  that need approval under `approvals`. Without it they run directly as above;
  both paths go through one function (`actions.perform`).

The module runs a user service on **127.0.0.1:18090** (`control.port`). A reverse proxy must route
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

`moonshine-pair` shows a Moonlight pairing card: start pairing in Moonlight, then
type its 4-digit PIN in the hub. The API reads waiting client IDs from
`moonshine.service`'s journal (last 3 minutes, so the control user needs journal
access, e.g. `wheel`), sends the PIN only to the newest one (falling back to
Moonlight's fixed ID `0123456789ABCDEF`) at `control.moonshineUrl`'s
`/submit-pin`, and reports when nothing is waiting. Moonlight itself confirms
whether the PIN was correct.

Keep the network restriction: it also protects the first-run setup page.
The API requires matching Host/Origin, JSON, and `X-Erebor-Control: 1` for
POST/PUT/DELETE, bounds bodies (256 bytes for actions, 2 KiB for client
reports, 4 KiB for accounts), and disables caching.

`GET /api/control/status` reads the session environment once, runs its probes
concurrently, and shares the result between clients for `DASHBOARD_STATUS_TTL`
seconds (default 2); an action clears it.

### Errors

Failures are JSON `{"error": "<sentence>", "code": "<code>"}`. The sentence is
for people and may change; the code is stable. Refusals are 403, unavailable
services 503, timeouts 504.

| Code | Status | Meaning |
|---|---|---|
| `disabled`, `not-permitted` | 403 | Action not enabled on this host / not granted to this account |
| `forbidden` | 403 | The system control daemon's policy refuses the action |
| `approval-denied` | 403 | The owner declined the approval |
| `approval-timeout` | 403 | Nobody approved in time |
| `approval-cancelled` | 403 | The approval request was cancelled |
| `approval-unavailable` | 403 | No approval method is reachable (approvald's `no-method`) |
| `approval-busy` | 403 | Another approval is still waiting |
| `approval-rate-limited` | 403 | Too many approval requests |
| `approval-forbidden`, `approval-bad-request`, `approval-failed` | 403 | The approval service refused or failed |
| `no-session` | 503 | The daemon has no desktop session to act in |
| `session-timeout` | 504 | The desktop session did not answer the daemon |
| `unavailable` | 503 | The daemon does not offer this action on this machine |
| `sysd-bad-request` | 502 | The daemon did not understand the request |
| `sysd-failed` | 503 | The daemon could not complete the action |
| `sysd-permission` | 503 | The service's user is not (yet) in the socket's group: log out and in, or reboot |
| `sysd-down`, `sysd-unreachable` | 503 | The daemon is not running / cannot be reached |
| `sysd-timeout` | 504 | The daemon did not answer in time |
| `no-desktop`, `no-display`, `unsupported` | 503 | No supported compositor, no physical display, or no screen switcher |
| `wled-unreachable`, `wled-rejected`, `hyperhdr-unreachable`, `hyperhdr-rejected`, `not-configured` | 503 | Ambilight hardware problems |
| `timeout` | 504 | A local command did not finish in time |
| `failed` | 503 | Anything else (details only in the service log) |
| `no-client`, `moonshine-unreachable` | 409, 503 | Moonlight pairing: nothing waiting / Moonshine down |
| `tasks-unavailable` | 503 | The task board command failed or printed something else |
| `rate-limited` | 429 | Too many client error reports |
| `unknown-action`, `unknown-screen`, `unknown-profile` | 400 | The catalog, `control.screenModes` or the profiles do not know this id |

The dashboard shows the sentence for every failure; the approval codes
`approval-denied`, `-timeout`, `-cancelled`, `-unavailable`, `-busy` and
`-rate-limited`, and `forbidden`, `disabled` and `not-permitted`, appear as
warnings (refused on purpose), every other code as an error. A 403 without a
code (older APIs) counts as a refusal.

Validation errors keep their plain `{"error": "Invalid action"}` (or
`"Invalid request"`) with status 400.

### Logging

The service logs single lines (`logger: message key=value ...`) to stderr; under
systemd each line carries a syslog priority, so `journalctl --user -u
dashboard-control -p warning` shows only problems. `control.logLevel`
(`DASHBOARD_LOG_LEVEL`, default `INFO`) sets the level.

- `dashboard.access`: every mutating request and every non-2xx response, with
  method, path, status, duration and account. Successful polls appear only at
  `DEBUG`.
- `dashboard.audit`: sign-in success, failure and lockout (with the proxy's
  client address), first-run setup, logout, password changes, account
  creation/update/deletion, every action (result `ok`, `refused` or `failed`,
  code, path `direct` or `sysd`, duration) and Moonlight pairing attempts.
  Passwords and PINs are never logged.
- `dashboard`: failures with a reason; a condition that repeats on every
  refresh (such as a broken task board) is logged once an hour. Tracebacks only
  appear for unexpected errors, at `ERROR`.
- `dashboard.client`: frontend errors reported by signed-in browsers to
  `POST /api/control/client-log` with `{"message", "kind"?: "error" |
  "unhandledrejection" | "http" | "other", "source"?, "line"?, "column"?,
  "stack"?}` (≤ 2 KiB, 5 at once then 10 a minute per account, 60 a minute
  overall; fields are cut short and control characters removed), at `WARNING`.

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

The standalone command (`erebor-dashboard-control`, or `python -m control` in
`api/`) reads its settings from the environment, which the module sets from its
options: `DASHBOARD_HOSTS` and `DASHBOARD_ACTIONS` (comma-separated),
`DASHBOARD_PORT`, `DASHBOARD_LOG_LEVEL`, `DASHBOARD_SETTINGS` (JSON file),
`DASHBOARD_STATE_DIR`, `DASHBOARD_WLED_URL`, `DASHBOARD_HYPERHDR`,
`DASHBOARD_IGNORED_OUTPUTS`, `DASHBOARD_SCREEN_MODES` (JSON object),
`DASHBOARD_MOONSHINE_URL`, `DASHBOARD_TASKS_COMMAND` (JSON argv),
`DASHBOARD_TASKS_LINK_BASE`, `DASHBOARD_MODES_STATE` and `DASHBOARD_SYSD_SOCKET`.

### Task board

Set `control.tasks.command` to a command (argv) that prints a task board as JSON,
and optionally `control.tasks.linkBase` to a URL prefix for its task paths.
Administrators then get a read-only **Tasks** card, folded by default to one summary line (unfolding is remembered per device): counts per status,
overdue reviews, epic progress, and the active or overdue tasks
(all open tasks on request), each linking to `linkBase` + its path. Other accounts
neither see the card nor can read `GET /api/control/tasks` (403). The API runs the
command on each refresh (every minute while visible, 10-second timeout).

The command's output (extra fields are ignored and never passed on):

```json
{
  "schema": 1,
  "statuses": [{"id": "ACTIVE", "label": "Active"}, {"id": "READY", "label": "Ready"}],
  "counts": {"ACTIVE": 1, "READY": 2, "OPEN": 5},
  "overdue": 1,
  "epics": [{"id": "ABC-1", "title": "…", "status": "OPEN", "path": "tasks/ABC-1.md",
             "overdue": false, "review": "2026-01-31", "done": 2, "total": 6}],
  "tasks": [{"id": "ABC-2", "title": "…", "status": "READY", "path": "tasks/ABC-2.md",
             "overdue": false, "review": "2026-01-31", "epic": "ABC-1", "kind": "…"}]
}
```

- A status is any id matching `^[A-Z][A-Z_-]{0,15}$`; a new status never
  breaks the board. `statuses` (optional) gives the display order and labels;
  without it the order of `counts` is used and labels are derived (`ON_HOLD` →
  "On hold"). The response always has `statuses`, covering every status in
  `counts` and the tasks. Clients must render unknown statuses generically.
- Each entry is validated on its own: an invalid one (bad id, unknown field
  type, title over 200 characters) is dropped and logged once. Lists are cut at
  1000 tasks, 100 epics and 32 statuses. Only a wrong top-level shape (no
  object, `counts` not an object, `epics`/`tasks` not lists, `overdue` not a
  count) makes the endpoint answer 503 (`tasks-unavailable`).
- Returned fields: `counts`, `statuses`, `overdue`, and `epics`/`tasks` entries
  with `id`, `title`, `status`, `overdue` and optional `review`, `epic`, `kind`,
  `url`; epics add `done` and `total`. Paths must be relative `.md` paths
  without `..` to get a `url`.
- Contract check for a producer: `python -c 'import json, sys; from
  control.tasks import parse_board; parse_board(json.load(sys.stdin),
  strict=True)'` in `api/` fails on anything the API would drop.

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

## Development

Frontend sources are in `frontend/`, the API package and its tests in `api/`
(`api/control/`, one module per concern: `boundary` middleware, `accounts`,
`layout`, `tasks`, `moonshine`, `status`, `actions`, `clientlog`, the
integrations `display`, `power`, `ambilight`, `modes`, `sysd` and the
`compositors` adapters), and Nix definitions in `nix/`. Run `npm ci && npm run
build` inside `frontend/` for a non-Nix frontend build. `nix flake check` builds
the default frontend, runs the API tests (`python -m unittest discover -s api`)
without issuing system commands, and evaluates the NixOS module. Review
package-lock changes and update `npmDepsHash` when changing dependencies. The
version for both packages and the API is in `VERSION`.

A NixOS configuration takes this repository as a flake input pinned to a
commit: push here, then update the consumer's input (`nix flake update
erebor-dashboard`, or the revision in its `flake.nix`) and rebuild.
