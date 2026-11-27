import { ErrorHandler, Injectable } from '@angular/core';

// Uncaught errors and unhandled rejections go to the control API's client log, so failures on
// phones and installed apps show up somewhere. Signed-in sessions only, at most LIMIT per minute
// per page, and never retried: an older API without the endpoint (404) just turns reporting off.
const ENDPOINT = '/api/control/client-log';
const LIMIT = 5;
const WINDOW_MS = 60000;
// The API's limits: it cuts message, source and stack to these lengths and refuses bodies over 2 KiB.
const MAX = { message: 300, source: 200, stack: 600 };
const MAX_BODY = 2048;
const MAX_POSITION = 10_000_000;
type Kind = 'error' | 'unhandledrejection' | 'http' | 'other';

const sent: number[] = [];
let enabled: () => boolean = () => false;
let unsupported = false;

const clip = (value: unknown, max: number) => (typeof value === 'string' ? value : value == null ? '' : String(value)).slice(0, max);

function describe(error: unknown) {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (typeof error === 'string') return error;
  try { return JSON.stringify(error) ?? String(error); } catch { return String(error); }
}

const position = (value?: number) => Number.isInteger(value) ? Math.min(Math.max(value!, 0), MAX_POSITION) : 0;

/** The report as JSON within the body limit: non-ASCII text or escapes can make it longer than its characters. */
function encode(report: { kind: Kind; message: string; source: string; line: number; column: number; stack: string }) {
  let body = JSON.stringify(report);
  for (const field of ['stack', 'source', 'message'] as const) {
    while (new TextEncoder().encode(body).length > MAX_BODY && report[field]) {
      report[field] = report[field].slice(0, Math.floor(report[field].length / 2));
      body = JSON.stringify(report);
    }
  }
  return body;
}

export function reportError(error: unknown, where: { source?: string; line?: number; column?: number; kind?: Kind } = {}) {
  try {
    if (unsupported || !enabled()) return;
    const now = Date.now();
    while (sent.length && now - sent[0] > WINDOW_MS) sent.shift();
    if (sent.length >= LIMIT) return;
    sent.push(now);
    const body = encode({
      kind: where.kind ?? 'error',
      message: clip(describe(error), MAX.message) || 'Unknown error',
      source: clip(where.source, MAX.source),
      line: position(where.line),
      column: position(where.column),
      stack: clip(error instanceof Error ? error.stack : '', MAX.stack),
    });
    void fetch(ENDPOINT, {
      method: 'POST', cache: 'no-store', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Erebor-Control': '1' },
      body, signal: AbortSignal.timeout(5000),
    }).then(response => { if (response.status === 404) unsupported = true; })
      // Best effort: a report that cannot be delivered must not cause another one.
      .catch(() => undefined);
  } catch {
    // Reporting must never throw from inside an error handler.
  }
}

/** Start reporting; `signedIn` is checked for every report. */
export function startErrorReporting(signedIn: () => boolean) {
  enabled = signedIn;
  addEventListener('error', event => {
    // Cross-origin scripts only say "Script error." with no details worth sending.
    if (!event.error && event.message === 'Script error.') return;
    reportError(event.error ?? event.message, { source: event.filename, line: event.lineno, column: event.colno });
  });
  addEventListener('unhandledrejection', event => reportError(event.reason, { kind: 'unhandledrejection' }));
}

/** Errors Angular catches itself (templates, event handlers) never reach window.onerror. */
@Injectable()
export class ReportingErrorHandler extends ErrorHandler {
  override handleError(error: unknown) {
    super.handleError(error);
    reportError(error, { source: 'angular' });
  }
}
