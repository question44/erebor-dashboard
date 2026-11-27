# Changelog

## 2.1.0 (unreleased)

Control API:

- Task board: any status id (`^[A-Z][A-Z_-]{0,15}$`) is accepted, so `READY`
  and later statuses no longer fail the whole board with 503. Entries are
  validated one by one; an invalid one is dropped and logged once. The
  response gains `statuses` (display order and labels, passed through from the
  producer or derived from `counts`). Only a wrong top-level shape fails.
- Errors: every refusal and failure has a clear sentence and a stable `code`
  (`{"error", "code"}`), including each approvald outcome
  (`approval-denied`, `approval-timeout`, `approval-unavailable` for
  approvald's `no-method`, `approval-busy`, …), a socket the service's user may not open yet
  (group membership not picked up), a stopped daemon and timeouts.
- Logging: stdlib `logging` with single-line `key=value` records and journald
  priorities; uvicorn's access log is replaced by one that skips successful
  polls; audit records for sign-in, setup, logout, password and account
  changes, actions and Moonlight pairing (never passwords or PINs);
  `control.logLevel`. New `POST /api/control/client-log` for frontend errors.
- Structure: `api/control.py` is now the `control` package (boundary,
  accounts, layout, tasks, moonshine, status, actions, client log and the
  integrations). Compositor support sits behind an adapter interface with one
  adapter, Umbriel; Niri and Mango support is removed. Ignored virtual
  outputs, screen mode labels and the HyperHDR address are options
  (`control.ignoredOutputs`, `control.screenModes`, `control.hyperhdrAddress`)
  with the previous values as defaults; `control.port` sets the port.
- One action catalog, `actions.json`, read by the API and by the module for
  `enabledActions`' type.
- `/api/control/status` reads the session environment once, runs its probes
  concurrently and shares results for 2 s; actions clear the cache.
  Moonshine's pairing status shares one journal read for 3 s.
- SQLite runs in WAL mode; reads (session lookups, layouts) no longer take the
  write lock. At most two password hashes run at once.
- The Sequoia page background's backend is removed (`GET
  /api/control/sequoia`, `config.sequoia`, the `control.sequoiaData` option);
  every response is now `Cache-Control: no-store`.
- The redundant `X-Erebor-Control` parameter of `POST /api/control` is gone
  (the boundary middleware still requires the header).
- One version (`VERSION`) for both packages and the API.

Frontend:

- The Sequoia and transparent page backgrounds, the liquid glass controls and
  menus and the task board's spring are removed (old per-device background
  settings are cleared once).
- One `api()` helper for every control API call. Failures show the server's
  sentence for any status (plain-text proxy answers too); refusals (the
  approval codes, `forbidden`, `disabled`, `not-permitted`, or a 403 without a
  code from older APIs) appear as warnings, everything else as errors. The
  "unavailable" page shows the server's reason whenever it gave one.
- Task board: statuses, their order and labels come from the API's
  `statuses`; any status renders, and a failed refresh keeps the last board
  marked as stale.
- Uncaught errors, unhandled rejections and errors Angular catches are sent to
  `POST /api/control/client-log` (`kind`, `message`, `source`, `line`,
  `column`, `stack`, cut to the API's 300/200/600 characters and its 2 KiB
  body limit), five a minute, signed-in sessions only.
- Action labels and which actions ask for confirmation come from
  `actions.json` (the frontend package's source now includes it); screen
  buttons use the API's `screens` labels, falling back to DP-1 and HDMI.
