import { ErrorHandler, Injectable } from '@angular/core';

// Uncaught errors and unhandled rejections go to the control API's client log, so failures on
// phones and installed apps show up somewhere. Signed-in sessions only, at most LIMIT per minute
// per page, and never retried: an older API without the endpoint (404) just turns reporting off.
const ENDPOINT = '/api/control/client-log';
const LIMIT = 5;
const WINDOW_MS = 60000;

const sent: number[] = [];
let enabled: () => boolean = () => false;
let unsupported = false;

const clip = (value: unknown, max: number) => (typeof value === 'string' ? value : value == null ? '' : String(value)).slice(0, max);

function describe(error: unknown) {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (typeof error === 'string') return error;
  try { return JSON.stringify(error) ?? String(error); } catch { return String(error); }
}

export function reportError(error: unknown, where: { source?: string; line?: number; column?: number } = {}) {
  try {
    if (unsupported || !enabled()) return;
    const now = Date.now();
    while (sent.length && now - sent[0] > WINDOW_MS) sent.shift();
    if (sent.length >= LIMIT) return;
    sent.push(now);
    const body = {
      message: clip(describe(error), 300),
      source: clip(where.source, 300),
      line: Number.isInteger(where.line) ? where.line : 0,
      column: Number.isInteger(where.column) ? where.column : 0,
      stack: clip(error instanceof Error ? error.stack : '', 1000),
    };
    void fetch(ENDPOINT, {
      method: 'POST', cache: 'no-store', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Erebor-Control': '1' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
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
  addEventListener('unhandledrejection', event => reportError(event.reason, { source: 'unhandledrejection' }));
}

/** Errors Angular catches itself (templates, event handlers) never reach window.onerror. */
@Injectable()
export class ReportingErrorHandler extends ErrorHandler {
  override handleError(error: unknown) {
    super.handleError(error);
    reportError(error, { source: 'angular' });
  }
}
