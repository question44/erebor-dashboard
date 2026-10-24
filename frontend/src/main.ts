import { Component, DestroyRef, HostListener, Input, TemplateRef, ViewChild, inject, signal } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { A11yModule } from '@angular/cdk/a11y';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatChipsModule } from '@angular/material/chips';

interface Action { id: string; label: string; icon: string; off?: boolean; danger?: boolean; confirm?: string; }
interface InstallPrompt extends Event { prompt(): Promise<void>; }
type Device = 'display' | 'ambilight';
type DeviceState = 'on' | 'off' | 'mixed' | 'unknown';
interface DeviceStatus { state: DeviceState; detail: string; }
interface DeviceSnapshot { display: DeviceStatus; ambilight: DeviceStatus; }
interface PowerStatus {
  state: 'awake' | 'asleep' | 'off' | 'unknown'; detail: string;
  operatingSystem?: { state: 'nixos' | 'windows' | 'unknown'; detail: string; };
}
interface Status { severity: 'info' | 'success' | 'error'; text: string; }

const paths: Record<string, string> = {
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
};
const availableActions = (): Action[] => [
  { id: 'wake', label: 'Wake up', icon: 'power' },
  { id: 'display-on', label: 'Display on', icon: 'display' },
  { id: 'display-off', label: 'Display off', icon: 'display', off: true },
  { id: 'ambilight-on', label: 'Ambilight on', icon: 'light' },
  { id: 'ambilight-off', label: 'Ambilight off', icon: 'light', off: true },
  { id: 'sleep', label: 'Sleep', icon: 'sleep', confirm: `Put ${settings.title} to sleep? Remote services will disconnect until it wakes.` },
  { id: 'reboot-windows', label: 'Reboot to Windows', icon: 'windows', danger: true, confirm: `Reboot ${settings.title} into Windows? Save your work first. All NixOS services will disconnect. This selects Windows for the next boot only.` },
  { id: 'power-off', label: 'Power off', icon: 'power', danger: true, confirm: `Power off ${settings.title}? Save your work first. Remote services will stop.` },
];
interface Service {
  id: string; label: string; description: string; icon: string;
  url?: string; subdomain?: string; port?: number; scheme?: string;
}
interface Settings {
  title: string; description: string; domainSuffix: string;
  actions: string[]; services: Service[];
  wakeUrl?: string;
}
const settings: Settings = await fetch('/config.json').then(response => {
  if (!response.ok) throw new Error('Dashboard configuration unavailable');
  return response.json();
});
const domain = settings.domainSuffix && location.hostname.endsWith(settings.domainSuffix);
const services = settings.services.map(service => ({ ...service,
  url: service.url ?? (domain && service.subdomain
    ? `https://${service.subdomain}.${location.hostname}`
    : `${service.scheme ?? 'http'}://${location.hostname}${service.port ? ':' + service.port : ''}`),
}));


@Component({
  selector: 'erebor-icon',
  template: `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path [attr.d]="paths[name]" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>`,
  styles: [`:host { display: inline-flex; width: 24px; height: 24px; flex-shrink: 0; } svg { width: 100%; height: 100%; }`],
})
class Icon {
  @Input({ required: true }) name = '';
  readonly paths = paths;
}

@Component({
  selector: 'erebor-app',
  imports: [Icon, A11yModule, MatButtonModule, MatDialogModule, MatChipsModule],
  templateUrl: './app.html',
})
class Dashboard {
  readonly settings = settings;
  readonly actions = availableActions().filter(action => settings.actions.includes(action.id)
    && (action.id !== 'wake' || settings.wakeUrl?.startsWith('https://')));
  readonly services = services;
  readonly deviceControls = (['display', 'ambilight'] as const).map(device => ({
    id: device,
    label: device === 'display' ? 'Display' : 'Ambilight',
    icon: device === 'display' ? 'display' : 'light',
    actions: this.actions.filter(action => action.id.startsWith(`${device}-`)),
  })).filter(device => device.actions.length);
  readonly systemActions = this.actions.filter(action => !this.deviceControls.some(device => device.actions.includes(action)));
  readonly hasWake = this.actions.some(action => action.id === 'wake');
  readonly power = signal<PowerStatus | null>(null);
  readonly checkingPower = signal(false);
  private powerRequest: AbortController | null = null;
  readonly online = signal(navigator.onLine);
  readonly busy = signal(false);
  readonly status = signal<Status | null>(null);
  readonly devices = signal<DeviceSnapshot | null>(null);
  readonly checkingDevices = signal(false);
  private deviceRequest: AbortController | null = null;
  readonly installPrompt = signal<InstallPrompt | null>(null);
  readonly confirmation = signal<Action | null>(null);
  readonly dialog = inject(MatDialog);
  @ViewChild('confirmDialog') confirmDialog!: TemplateRef<unknown>;

  constructor() {
    const scheme = matchMedia('(prefers-color-scheme: dark)');
    const update = () => document.querySelector('meta[name="theme-color"]')?.setAttribute('content', scheme.matches ? '#141218' : '#fdf8ff');
    update();
    scheme.addEventListener('change', update);
    document.title = `${settings.title} services`;
    void this.refreshDevices();
    void this.refreshPower();
    const poll = window.setInterval(() => {
      void this.refreshDevices();
      void this.refreshPower();
    }, 10000);
    inject(DestroyRef).onDestroy(() => {
      scheme.removeEventListener('change', update);
      window.clearInterval(poll);
      this.clearDevices();
      this.clearPower();
    });
  }

  @HostListener('window:online')
  @HostListener('window:offline')
  connectionChanged() {
    this.online.set(navigator.onLine);
    this.clearDevices();
    this.clearPower();
    if (this.online()) { void this.refreshDevices(); void this.refreshPower(); }
  }

  @HostListener('document:visibilitychange')
  visibilityChanged() {
    this.clearDevices();
    this.clearPower();
    if (!document.hidden) { void this.refreshDevices(); void this.refreshPower(); }
  }

  private clearDevices() {
    this.deviceRequest?.abort();
    this.deviceRequest = null;
    this.devices.set(null);
    this.checkingDevices.set(false);
  }

  private clearPower() {
    this.powerRequest?.abort();
    this.powerRequest = null;
    this.power.set(null);
    this.checkingPower.set(false);
  }

  powerLabel() {
    const state = this.power()?.state;
    return state ? { awake: 'Awake', asleep: 'Asleep', off: 'Powered off', unknown: 'Unknown' }[state]
      : this.checkingPower() ? 'Checking…' : 'Unavailable';
  }

  osLabel() {
    if (this.power()?.state !== 'awake') return '';
    const state = this.power()?.operatingSystem?.state;
    return state === 'nixos' ? 'NixOS' : state === 'windows' ? 'Windows' : 'OS unknown';
  }

  hostLabel() {
    return this.powerLabel() + (this.osLabel() ? ` · ${this.osLabel()}` : '');
  }

  hostDetail() {
    const power = this.power();
    if (!power) return this.checkingPower() ? 'Reading power state through NanoKVM…' : 'NanoKVM status unavailable. Check Tailscale connectivity.';
    return power.detail + (power.state === 'awake' ? ' ' + (power.operatingSystem?.detail ?? 'OS detection unavailable.') : '');
  }

  canWake() {
    const state = this.power()?.state;
    return state === 'asleep' || state === 'off';
  }

  async refreshPower() {
    if (!this.hasWake || !settings.wakeUrl || this.powerRequest || !this.online() || document.hidden || this.busy()) return;
    const controller = new AbortController();
    this.powerRequest = controller;
    this.checkingPower.set(true);
    try {
      const response = await fetch(new URL('status', settings.wakeUrl), {
        mode: 'cors', credentials: 'omit', cache: 'no-store',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      });
      if (!response.ok) throw new Error('Power status unavailable');
      const result: PowerStatus = await response.json();
      if (!['awake', 'asleep', 'off', 'unknown'].includes(result.state) || typeof result.detail !== 'string') throw new Error('Invalid power status');
      if (!controller.signal.aborted) this.power.set(result);
    } catch {
      if (!controller.signal.aborted) this.power.set(null);
    } finally {
      if (this.powerRequest === controller) {
        this.powerRequest = null;
        this.checkingPower.set(false);
      }
    }
  }

  async refreshDevices() {
    if (!this.actions.length || this.deviceRequest || !this.online() || document.hidden || this.busy()) return;
    const controller = new AbortController();
    this.deviceRequest = controller;
    this.checkingDevices.set(true);
    try {
      const response = await fetch('/api/control/status', {
        cache: 'no-store',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
      });
      if (!response.ok) throw new Error('Status unavailable');
      const snapshot: DeviceSnapshot = await response.json();
      if (!controller.signal.aborted) this.devices.set(snapshot);
    } catch {
      if (!controller.signal.aborted) this.devices.set(null);
    } finally {
      if (this.deviceRequest === controller) {
        this.deviceRequest = null;
        this.checkingDevices.set(false);
      }
    }
  }

  deviceLabel(device: Device) {
    if (!this.online()) return 'Offline';
    const state = this.devices()?.[device].state;
    if (!state) return this.checkingDevices() ? 'Checking…' : 'Unknown';
    return { on: 'On', off: 'Off', mixed: 'Mixed', unknown: 'Unknown' }[state];
  }

  deviceDetail(device: Device) {
    if (!this.online()) return 'Reconnect to refresh status.';
    return this.devices()?.[device].detail ?? (this.checkingDevices() ? 'Reading current state…' : 'Status unavailable.');
  }

  @HostListener('window:beforeinstallprompt', ['$event'])
  offerInstall(event: Event) {
    event.preventDefault();
    this.installPrompt.set(event as InstallPrompt);
  }

  @HostListener('window:appinstalled')
  installed() { this.installPrompt.set(null); }

  async install() {
    const prompt = this.installPrompt();
    this.installPrompt.set(null);
    await prompt?.prompt();
  }

  activate(action: Action) {
    if (this.busy() || !navigator.onLine) return;
    if (!action.confirm) { void this.execute(action); return; }
    this.confirmation.set(action);
    const ref = this.dialog.open(this.confirmDialog, { ariaLabelledBy: 'confirm-title', ariaDescribedBy: 'confirm-description', maxWidth: 'calc(100vw - 32px)' });
    ref.afterClosed().subscribe((confirmed: boolean) => {
      this.confirmation.set(null);
      if (confirmed) void this.execute(action);
    });
  }

  async execute(action: Action) {
    if (action.id === 'wake') { await this.wake(); return; }
    if (this.busy() || !navigator.onLine) return;
    this.busy.set(true);
    this.clearDevices();
    this.clearPower();
    this.status.set({ severity: 'info', text: 'Sending command…' });
    try {
      const response = await fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Erebor-Control': '1' },
        body: JSON.stringify({ action: action.id }),
        signal: AbortSignal.timeout(action.id === 'reboot-windows' ? 70000 : 20000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Command failed');
      this.status.set({ severity: 'success', text: `${action.label}: command sent.` });
    } catch (error) {
      this.status.set({ severity: 'error', text: action.confirm
        ? `Connection lost or command failed. Check whether ${settings.title} is asleep, powered off, or rebooting.`
        : error instanceof Error ? error.message : 'Command failed' });
    } finally {
      this.busy.set(false);
      void this.refreshDevices();
      void this.refreshPower();
    }
  }

  async wake() {
    if (this.busy() || !navigator.onLine || !settings.wakeUrl) return;
    this.busy.set(true);
    this.clearDevices();
    this.clearPower();
    this.status.set({ severity: 'info', text: 'Checking power state through NanoKVM…' });
    try {
      const response = await fetch(settings.wakeUrl, {
        method: 'POST', mode: 'cors', credentials: 'omit', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-Erebor-Wake': '1' },
        body: '{}', signal: AbortSignal.timeout(20000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Wake command failed');
      if (result.state === 'already-on') {
        this.status.set({ severity: 'info', text: 'Power LED is steadily on. No button press sent. The PC may already be running.' });
        return;
      }
      if (result.state !== 'wake-sent') throw new Error('Unexpected NanoKVM response');
      this.status.set({ severity: 'info', text: 'Wake command sent. Waiting for the dashboard server…' });
      const deadline = Date.now() + 90000;
      while (Date.now() < deadline) {
        await new Promise(resolve => window.setTimeout(resolve, 2000));
        try {
          const check = await fetch('/api/control/status', { cache: 'no-store', signal: AbortSignal.timeout(3000) });
          if (check.ok) {
            const snapshot: DeviceSnapshot = await check.json();
            if (snapshot.display && snapshot.ambilight) {
              this.devices.set(snapshot);
              this.status.set({ severity: 'success', text: `${settings.title} is awake and its dashboard server is responding.` });
              return;
            }
          }
        } catch { /* Keep waiting while the host resumes or boots. */ }
      }
      this.status.set({ severity: 'info', text: 'Wake command was sent, but the dashboard server has not responded yet. Check NanoKVM; another operating system may have started.' });
    } catch (error) {
      this.status.set({ severity: 'error', text: error instanceof TypeError
        ? 'Could not reach NanoKVM. Check Tailscale and allow local network access if your browser asks. The wake result is unknown.'
        : error instanceof Error ? error.message : 'Wake command failed' });
    } finally {
      this.busy.set(false);
      void this.refreshDevices();
      void this.refreshPower();
    }
  }
}

bootstrapApplication(Dashboard).catch(error => console.error('Dashboard unavailable:', error));
if ('serviceWorker' in navigator && isSecureContext) {
  navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
    .catch(error => console.error('Offline support unavailable:', error));
}
