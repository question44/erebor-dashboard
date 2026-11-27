import catalog from '../../actions.json';

export interface Action { id: string; label: string; icon: string; off?: boolean; danger?: boolean; confirm?: string; button?: string; }
export interface Service {
  id: string; label: string; description: string; icon: string;
  url?: string; subdomain?: string; port?: number; scheme?: string;
  /** Dashboard hostnames on which this HTTPS service opens in an in-page window instead of a new tab. */
  embedOn?: string[];
}
export interface Account { username: string; admin: boolean; }
/** Per-account arrangement of quick-action cards and services; IDs not listed keep the default order. */
export type Section = 'cards' | 'services';
export interface Layout {
  cards: string[]; services: string[]; hiddenCards: string[]; hiddenServices: string[]; showHidden: boolean;
  /** Sections collapsed to their first `foldRows` rows (default 1). */
  folded: Section[]; foldRows: Partial<Record<Section, number>>;
}
export const emptyLayout = (): Layout => ({ cards: [], services: [], hiddenCards: [], hiddenServices: [], showHidden: false,
  folded: [], foldRows: {} });
export interface Settings {
  title: string; description: string; domainSuffix: string;
  actions: string[]; services: Service[];
  wakeUrl?: string;
  outletUrl?: string;
  /** Present when the control API manages accounts. */
  auth?: boolean;
  /** Whether the power/OS indicator is shown; absent in single-user deployments. */
  hostStatus?: boolean;
  user?: Account;
  layout?: Layout;
  /** Administrators only: a read-only task board is available from the API. */
  taskBoard?: boolean;
  /** Actions the system control daemon asks the owner to approve first. */
  approvals?: Record<string, 'approve'>;
  /** Screen switcher modes (`screen-<mode>` actions) and their labels, e.g. { dp: 'DP-1' }. */
  screens?: Record<string, string>;
}
/** An upper-case task status such as ACTIVE, READY, OPEN or REVIEW; new ones may appear at any time. */
export type TaskStatus = string;
export interface TaskItem {
  id: string; title: string; status: TaskStatus; overdue: boolean;
  review?: string; epic?: string; kind?: string; url?: string;
}
export interface TaskEpic extends TaskItem { done: number; total: number; }
export interface TaskBoard {
  /** Tasks per status; older APIs send exactly ACTIVE, OPEN and REVIEW. */
  counts: Partial<Record<TaskStatus, number>>;
  overdue: number; epics: TaskEpic[]; tasks: TaskItem[];
  /** The API's statuses in display order, when it sends them. */
  statuses?: { id: TaskStatus; label: string }[];
}
/** Labels for APIs that send no `statuses` list (before 2.1, exactly ACTIVE, OPEN and REVIEW); otherwise the API's labels win. */
export const LEGACY_STATUS_LABELS: Record<string, string> = { REVIEW: 'In review' };
export const titleCase = (id: string) => id.toLowerCase().split(/[_\s-]+/).filter(Boolean)
  .map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
export type PublicSettings = Pick<Settings, 'title' | 'description' | 'domainSuffix' | 'auth'> & Partial<Settings>;

export const MODULES: Record<string, { label: string; description: string }> = {
  'host-status': { label: 'Host power status', description: 'Awake/asleep indicator and detected operating system' },
  'outlet': { label: 'Power usage', description: 'Live watts and energy from the outlet' },
  'quick-actions': { label: 'Quick actions', description: 'Display, Ambilight, performance profile, Moonlight pairing, wake and power controls' },
  'services': { label: 'Services', description: 'Links to web services' },
};

export const paths: Record<string, string> = {
  display: 'M3 4h18v12H3z M8 20h8 M12 16v4',
  light: 'M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5 M16 12a4 4 0 1 0-8 0 4 4 0 0 0 8 0',
  sleep: 'M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13Z',
  power: 'M12 3v9 M5.6 5.6a9 9 0 1 0 12.8 0',
  media: 'M5 3h14v18H5z M10 8l6 4-6 4z',
  cloud: 'M7 18h11a4 4 0 0 0 0-8 6 6 0 0 0-11-2 5 5 0 0 0 0 10Z',
  sync: 'M3 10a9 9 0 0 1 15-5l3 3 M21 3v5h-5 M21 14a9 9 0 0 1-15 5l-3-3 M3 21v-5h5',
  torrent: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  code: 'm8 6-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18',
  system: 'M4 4h16v12H4z M8 20h8 M12 16v4 M8 8l3 2-3 2 M13 12h3',
  windows: 'M3 5l8-1v8H3z M13 3.7l8-1v9.3h-8z M3 14h8v8l-8-1z M13 14h8v9.3l-8-1z',
  arrow: 'M7 17 17 7 M7 7h10v10',
  gamepad: 'M6 9h4 M8 7v4 M15 10h.01 M18 8h.01 M7 5h10a5 5 0 0 1 4.9 6l-1 5a3 3 0 0 1-5.2 1.4L14 16h-4l-1.7 1.4A3 3 0 0 1 3.1 16l-1-5A5 5 0 0 1 7 5Z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M4 21a8 8 0 0 1 16 0',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M2 21a7 7 0 0 1 14 0 M16 3.5a4 4 0 0 1 0 7.5 M18 14a7 7 0 0 1 4 7',
  key: 'M15 7a3 3 0 1 1 0 .01 M13 9.5 3 19.5V22h3v-2h2v-2h2l3.5-3.5',
  logout: 'M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4 M10 17l-5-5 5-5 M5 12h11',
  back: 'M19 12H5 M12 19l-7-7 7-7',
  plus: 'M12 5v14 M5 12h14',
  edit: 'M4 20h4L19 9l-4-4L4 16z M14 6l4 4',
  trash: 'M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  close: 'M6 6l12 12 M18 6 6 18',
  grip: 'M5 9h14 M5 15h14',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  'eye-off': 'M3 3l18 18 M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1 M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.8 9.8 0 0 0 5.4-1.6 M9.9 9.9a3 3 0 0 0 4.2 4.2',
  reset: 'M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5',
  check: 'M5 12.5l4.5 4.5L19 7',
  chevron: 'M6 9l6 6 6-6',
  'fold-grip': 'M8 5l4-3 4 3 M8 19l4 3 4-3 M4 12h16',
  maximize: 'M4 4h16v16H4z',
  minimize: 'M5 19h14',
  tabs: 'M3 9h18v11H3z M3 9V5h7v4 M10 5h6v4',
  split: 'M3 10h12v10H3z M8 10V4h13v11h-6',
  restore: 'M8 4h12v12 M4 8h12v12H4z',
  external: 'M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6',
  gauge: 'M12 14l4-4 M3.3 19a10 10 0 1 1 17.4 0',
  tasks: 'M9 6h11 M9 12h11 M9 18h11 M4 6l1 1 2-2 M4 12l1 1 2-2 M4 18l1 1 2-2',
};

/** Labels the screen buttons had before the API sent a `screens` map. */
const DEFAULT_SCREENS: Record<string, string> = { dp: 'DP-1', hdmi: 'HDMI' };
export const screenLabel = (mode: string, screens?: Record<string, string>) =>
  screens?.[mode] || DEFAULT_SCREENS[mode] || mode.toUpperCase();

const KIND_ICONS: Record<string, string> = { power: 'power', display: 'display', screen: 'display', ambilight: 'light',
  profile: 'gauge', moonshine: 'gamepad' };

/** How actions look here; the list, labels and whether one asks first come from actions.json. */
const PRESENTATION: Record<string, Partial<Action> & { prompt?: (title: string) => string }> = {
  'display-off': { off: true },
  'ambilight-off': { off: true },
  'profile-power-saver': { button: 'Saver' },
  'profile-balanced': { button: 'Balanced' },
  'profile-performance': { button: 'Performance' },
  'sleep': { icon: 'sleep', prompt: title => `Put ${title} to sleep? Remote services will disconnect until it wakes.` },
  'reboot-windows': { icon: 'windows', danger: true, prompt: title => `Reboot ${title} into Windows? Save your work first. All NixOS services will disconnect. This selects Windows for the next boot only.` },
  'power-off': { danger: true, prompt: title => `Power off ${title}? Save your work first. Remote services will stop.` },
};

export function availableActions(title: string, screens?: Record<string, string>): Action[] {
  return catalog.actions.map(({ id, label, kind, confirm }) => {
    const { prompt, ...look } = PRESENTATION[id] ?? {};
    const action: Action = { icon: KIND_ICONS[kind] ?? 'system', ...look, id, label };
    if (kind === 'screen') {
      action.button = screenLabel(id.slice('screen-'.length), screens);
      action.label = `Show on ${action.button}`;
    }
    if (confirm) action.confirm = prompt?.(title) ?? `${label}?`;
    return action;
  });
}

export const actionLabel = (id: string, screens?: Record<string, string>) =>
  availableActions('', screens).find(action => action.id === id)?.label ?? id;

export function resolveServices(settings: Settings) {
  const domain = settings.domainSuffix && location.hostname.endsWith(settings.domainSuffix);
  return settings.services.map(service => ({ ...service,
    url: service.url ?? (domain && service.subdomain
      ? `https://${service.subdomain}.${location.hostname}`
      : `${service.scheme ?? 'http'}://${location.hostname}${service.port ? ':' + service.port : ''}`),
  }));
}

/** How a request failed: an HTTP error answer, no connection, no answer in time, or an unreadable answer. */
export type ApiFailure = 'http' | 'network' | 'timeout' | 'invalid';

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly kind: ApiFailure = 'http',
    /** Machine-readable reason, when the API sends one (e.g. "approval-denied", "sysd-down"). */
    readonly code?: string,
    /** The message is the server's own explanation (its `error` field or plain-text body), not a generic one. */
    readonly explained = false) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Error codes (control API 2.1, see its README) for requests refused on purpose, shown as warnings:
 * the approval was declined, cancelled, unreachable, timed out, busy or rate limited, or a policy
 * (the dashboard's or the system control daemon's) forbids the action. Every other code is a failure.
 */
export const REFUSAL_CODES = new Set(['approval-denied', 'approval-timeout', 'approval-cancelled', 'approval-unavailable',
  'approval-busy', 'approval-rate-limited', 'forbidden', 'disabled', 'not-permitted']);

/** A deliberate refusal; APIs without error codes signal those with 403. */
export const isRefusal = (error: unknown): error is ApiError => error instanceof ApiError
  && (error.code ? REFUSAL_CODES.has(error.code) : error.status === 403);

export const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError';

/** The failure of a fetch() itself: the caller's abort passes through, the rest becomes an ApiError. */
export function requestFailure(error: unknown, signal?: AbortSignal, server = 'the dashboard server'): unknown {
  if (signal?.aborted || isAbort(error)) return error;
  if (error instanceof DOMException && error.name === 'TimeoutError') return new ApiError(`${capitalize(server)} did not answer in time.`, 0, 'timeout');
  if (error instanceof TypeError) return new ApiError(`Could not reach ${server}.`, 0, 'network');
  return error;
}

/** A message for the user from any error, never a raw "signal timed out" or JSON parse error. */
export function errorText(error: unknown, fallback: string): string {
  const failure = requestFailure(error);
  if (failure instanceof ApiError || (failure instanceof Error && !(failure instanceof SyntaxError) && failure.message)) return failure.message;
  return fallback;
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The body of a response as JSON, or null when it is empty or not JSON (e.g. a proxy's HTML error page). */
export async function readJson(response: Response): Promise<any> {
  const text = await response.text();
  try { return text ? JSON.parse(text) : null; } catch { return null; }
}

export interface ApiOptions { timeout?: number; signal?: AbortSignal; }

/** Same-origin JSON call to the control API, satisfying its CSRF boundary. Every failure is an ApiError, except the caller's own abort. */
export async function api<T>(path: string, method = 'GET', body?: unknown, options: number | ApiOptions = {}): Promise<T> {
  const { timeout = 15000, signal } = typeof options === 'number' ? { timeout: options } : options;
  const deadline = AbortSignal.timeout(timeout);
  let response: Response, text: string;
  try {
    response = await fetch(`/api/control${path}`, {
      method, cache: 'no-store', credentials: 'same-origin',
      headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-Erebor-Control': '1' },
      body: method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(body ?? {}),
      signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
    });
    text = await response.text();
  } catch (error) { throw requestFailure(error, signal); }
  let result: any = null;
  try { result = text ? JSON.parse(text) : null; } catch { /* Proxy errors are plain text or HTML. */ }
  if (!response.ok) {
    const generic = `The dashboard server answered ${response.status}${response.statusText ? ' ' + response.statusText : ''}.`;
    const plain = result === null && text.trim() && !/^\s*</.test(text) ? text.trim().slice(0, 200) : '';
    const message = typeof result?.error === 'string' && result.error ? result.error
      : plain || generic;
    throw new ApiError(message, response.status, 'http', typeof result?.code === 'string' ? result.code : undefined,
      message !== generic);
  }
  if (result === null) throw new ApiError('Unexpected response from the dashboard server.', response.status, 'invalid');
  return result as T;
}
