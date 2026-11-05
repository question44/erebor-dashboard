export interface Action { id: string; label: string; icon: string; off?: boolean; danger?: boolean; confirm?: string; button?: string; }
export interface Service {
  id: string; label: string; description: string; icon: string;
  url?: string; subdomain?: string; port?: number; scheme?: string;
}
export interface Account { username: string; admin: boolean; }
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
}
export type PublicSettings = Pick<Settings, 'title' | 'description' | 'domainSuffix' | 'auth'> & Partial<Settings>;

export const MODULES: Record<string, { label: string; description: string }> = {
  'host-status': { label: 'Host power status', description: 'Awake/asleep indicator and detected operating system' },
  'outlet': { label: 'Power usage', description: 'Live watts and energy from the outlet' },
  'quick-actions': { label: 'Quick actions', description: 'Display, Ambilight, wake and power controls' },
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
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M4 21a8 8 0 0 1 16 0',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M2 21a7 7 0 0 1 14 0 M16 3.5a4 4 0 0 1 0 7.5 M18 14a7 7 0 0 1 4 7',
  key: 'M15 7a3 3 0 1 1 0 .01 M13 9.5 3 19.5V22h3v-2h2v-2h2l3.5-3.5',
  logout: 'M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4 M10 17l-5-5 5-5 M5 12h11',
  back: 'M19 12H5 M12 19l-7-7 7-7',
  plus: 'M12 5v14 M5 12h14',
  edit: 'M4 20h4L19 9l-4-4L4 16z M14 6l4 4',
  trash: 'M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
};

export const availableActions = (title: string): Action[] => [
  { id: 'wake', label: 'Wake up', icon: 'power' },
  { id: 'display-on', label: 'Display on', icon: 'display' },
  { id: 'display-off', label: 'Display off', icon: 'display', off: true },
  { id: 'screen-dp', label: 'Show on DP-1', icon: 'display', button: 'DP-1' },
  { id: 'screen-hdmi', label: 'Show on HDMI', icon: 'display', button: 'HDMI' },
  { id: 'ambilight-on', label: 'Ambilight on', icon: 'light' },
  { id: 'ambilight-off', label: 'Ambilight off', icon: 'light', off: true },
  { id: 'sleep', label: 'Sleep', icon: 'sleep', confirm: `Put ${title} to sleep? Remote services will disconnect until it wakes.` },
  { id: 'reboot-windows', label: 'Reboot to Windows', icon: 'windows', danger: true, confirm: `Reboot ${title} into Windows? Save your work first. All NixOS services will disconnect. This selects Windows for the next boot only.` },
  { id: 'power-off', label: 'Power off', icon: 'power', danger: true, confirm: `Power off ${title}? Save your work first. Remote services will stop.` },
];

export const actionLabel = (id: string) => availableActions('').find(action => action.id === id)?.label ?? id;

export function resolveServices(settings: Settings) {
  const domain = settings.domainSuffix && location.hostname.endsWith(settings.domainSuffix);
  return settings.services.map(service => ({ ...service,
    url: service.url ?? (domain && service.subdomain
      ? `https://${service.subdomain}.${location.hostname}`
      : `${service.scheme ?? 'http'}://${location.hostname}${service.port ? ':' + service.port : ''}`),
  }));
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

/** Same-origin JSON call to the control API, satisfying its CSRF boundary. */
export async function api<T>(path: string, method = 'GET', body?: unknown, timeout = 15000): Promise<T> {
  const response = await fetch(`/api/control${path}`, {
    method, cache: 'no-store', credentials: 'same-origin',
    headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-Erebor-Control': '1' },
    body: method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(body ?? {}),
    signal: AbortSignal.timeout(timeout),
  });
  const text = await response.text();
  let result: any = null;
  try { result = text ? JSON.parse(text) : null; } catch { /* Proxy errors are plain text. */ }
  if (!response.ok) throw new ApiError(result?.error ?? (text.trim().slice(0, 200) || `Request failed (${response.status})`), response.status);
  if (result === null) throw new ApiError('Unexpected response from the dashboard server', response.status);
  return result as T;
}
