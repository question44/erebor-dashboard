import { Component, DestroyRef, HostListener, TemplateRef, ViewChild, computed, inject, signal } from '@angular/core';
import { CdkDrag, CdkDragDrop, CdkDropList, moveItemInArray } from '@angular/cdk/drag-drop';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { A11yModule } from '@angular/cdk/a11y';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatChipsModule } from '@angular/material/chips';
import { MatMenuModule } from '@angular/material/menu';
import { Action, ApiError, Layout, Settings, api, availableActions, emptyLayout, resolveServices } from './config';
import { Icon } from './icon';
import { Session } from './session';
import { PasswordDialog } from './users';

interface InstallPrompt extends Event { prompt(): Promise<void>; }
type Device = 'display' | 'screen' | 'ambilight' | 'profile';
type DeviceState = 'on' | 'off' | 'mixed' | 'dp' | 'hdmi' | 'both' | 'none'
  | 'performance' | 'balanced' | 'power-saver' | 'unknown';
interface DeviceStatus { state: DeviceState; detail: string; }
interface DeviceSnapshot { display: DeviceStatus; screen: DeviceStatus; ambilight: DeviceStatus; profile?: DeviceStatus; }
interface PowerStatus {
  state: 'awake' | 'asleep' | 'off' | 'unknown'; detail: string;
  operatingSystem?: { state: 'nixos' | 'windows' | 'unknown'; detail: string; };
}
interface Status { severity: 'info' | 'success' | 'error'; text: string; }
interface OutletStatus { on: boolean; watts: number; todayKwh: number; monthKwh: number; }
type Arrangeable = 'cards' | 'services';
const HIDDEN = { cards: 'hiddenCards', services: 'hiddenServices' } as const;
// Layout for dashboards without accounts, where there is no server-side profile.
const LOCAL_LAYOUT_KEY = 'erebor-dashboard-layout';

/** Saved IDs first (dropping unknown ones), then anything new in its default position. */
const arrange = (ids: string[], saved: string[]) => [...new Set([...saved.filter(id => ids.includes(id)), ...ids])];

function readLocalLayout(): Layout {
  try { return { ...emptyLayout(), ...JSON.parse(localStorage.getItem(LOCAL_LAYOUT_KEY) ?? '{}') }; } catch { return emptyLayout(); }
}

@Component({
  selector: 'erebor-dashboard',
  imports: [Icon, A11yModule, MatButtonModule, MatDialogModule, MatChipsModule, MatMenuModule, MatSlideToggleModule,
    CdkDropList, CdkDrag],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  readonly session = inject(Session);
  readonly settings: Settings = this.session.settings()!;
  readonly actions = availableActions(this.settings.title).filter(action => this.settings.actions.includes(action.id)
    && (action.id !== 'wake' || this.settings.wakeUrl?.startsWith('https://')));
  readonly services = resolveServices(this.settings);
  readonly deviceControls = (['display', 'screen', 'ambilight', 'profile'] as const).map(device => ({
    id: device,
    label: { display: 'Display', screen: 'Screen', ambilight: 'Ambilight', profile: 'Performance' }[device],
    icon: { display: 'display', screen: 'display', ambilight: 'light', profile: 'gauge' }[device],
    actions: this.actions.filter(action => action.id.startsWith(`${device}-`)),
  })).filter(device => device.actions.length);
  readonly pairing = this.actions.find(action => action.id === 'moonshine-pair') ?? null;
  readonly systemActions = this.actions.filter(action => action !== this.pairing
    && !this.deviceControls.some(device => device.actions.includes(action)));
  readonly deviceById = Object.fromEntries(this.deviceControls.map(device => [device.id, device]));
  readonly cardIds = [...this.deviceControls.map(device => device.id), ...(this.pairing ? ['moonshine-pair'] : [])];
  private readonly serviceById = Object.fromEntries(this.services.map(service => [service.id, service]));
  readonly layout = signal<Layout>({ ...emptyLayout(), ...(this.settings.auth ? this.settings.layout : readLocalLayout()) });
  private readonly order = {
    cards: computed(() => arrange(this.cardIds, this.layout().cards)),
    services: computed(() => arrange(this.services.map(service => service.id), this.layout().services)),
  };
  readonly visibleCards = computed(() => this.visible('cards'));
  readonly visibleServices = computed(() => this.visible('services').map(id => this.serviceById[id]));
  readonly hiddenCount = computed(() => this.order.cards().filter(id => this.isHidden('cards', id)).length
    + this.order.services().filter(id => this.isHidden('services', id)).length);
  private saveTimer?: number;
  /** Edit mode: items wiggle, can be dragged and hidden; their own controls are paused. */
  readonly editing = signal(false);
  // Touch needs a short hold so a quick swipe still scrolls the page.
  readonly dragDelay = { touch: 180, mouse: 0 };
  /** Whether Moonshine has a Moonlight client waiting for a PIN; null when unknown. */
  readonly pairingWaiting = signal<boolean | null>(null);
  readonly pin = signal('');
  readonly pairingStatus = signal<Status | null>(null);
  private pairingRequest: AbortController | null = null;
  readonly canWakeHost = this.actions.some(action => action.id === 'wake');
  // The power LED check also gates host actions, even for accounts that cannot wake the host.
  readonly hasWake = (this.settings.wakeUrl?.startsWith('https://') ?? false) && (!!this.settings.auth || this.canWakeHost);
  readonly showHostStatus = this.hasWake && (this.settings.hostStatus ?? true);
  readonly hasOutlet = this.settings.outletUrl?.startsWith('https://') ?? false;
  readonly outlet = signal<OutletStatus | null>(null);
  readonly checkingOutlet = signal(false);
  private outletRequest: AbortController | null = null;
  readonly power = signal<PowerStatus | null>(null);
  readonly nixosResponding = signal(false);
  readonly checkingPower = signal(false);
  private powerRequest: AbortController | null = null;
  readonly online = signal(navigator.onLine);
  readonly busy = signal(false);
  readonly status = signal<Status | null>(null);
  readonly devices = signal<DeviceSnapshot | null>(null);
  readonly checkingDevices = signal(false);
  private deviceRequest: AbortController | null = null;
  readonly installPrompt = signal<InstallPrompt | null>(null);
  /** Running as an installed app (any standalone-like display mode, or iOS home screen). */
  readonly installed = signal(['standalone', 'fullscreen', 'minimal-ui', 'window-controls-overlay']
    .some(mode => matchMedia(`(display-mode: ${mode})`).matches) || (navigator as { standalone?: boolean }).standalone === true);
  readonly confirmation = signal<Action | null>(null);
  readonly dialog = inject(MatDialog);
  @ViewChild('confirmDialog') confirmDialog!: TemplateRef<unknown>;

  constructor() {
    document.title = `${this.settings.title} services`;
    void this.refreshDevices();
    void this.refreshPower();
    void this.refreshOutlet();
    const poll = window.setInterval(() => {
      void this.refreshDevices();
      void this.refreshPower();
    }, 10000);
    void this.refreshPairing();
    const outletPoll = window.setInterval(() => { void this.refreshOutlet(); void this.refreshPairing(); }, 5000);
    inject(DestroyRef).onDestroy(() => {
      this.flushLayout();
      window.clearInterval(poll);
      window.clearInterval(outletPoll);
      this.clearDevices();
      this.clearPower();
      this.clearOutlet();
    });
  }

  @HostListener('window:online')
  @HostListener('window:offline')
  connectionChanged() {
    this.online.set(navigator.onLine);
    this.clearDevices();
    this.clearPower();
    this.clearOutlet();
    if (this.online()) { void this.refreshDevices(); void this.refreshPower(); void this.refreshOutlet(); }
  }

  @HostListener('document:visibilitychange')
  visibilityChanged() {
    this.clearDevices();
    this.clearPower();
    this.clearOutlet();
    if (!document.hidden) { void this.refreshDevices(); void this.refreshPower(); void this.refreshOutlet(); }
  }

  private clearOutlet() {
    this.outletRequest?.abort();
    this.outletRequest = null;
    this.outlet.set(null);
    this.checkingOutlet.set(false);
  }

  async refreshOutlet() {
    if (!this.hasOutlet || !this.settings.outletUrl || this.outletRequest || !this.online() || document.hidden) return;
    const controller = new AbortController();
    this.outletRequest = controller;
    this.checkingOutlet.set(true);
    try {
      const response = await fetch(this.settings.outletUrl, {
        mode: 'cors', credentials: 'omit', cache: 'no-store',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]),
      });
      if (!response.ok) throw new Error('Outlet unavailable');
      const result: OutletStatus = await response.json();
      if (typeof result.on !== 'boolean' || [result.watts, result.todayKwh, result.monthKwh]
        .some(value => typeof value !== 'number' || !Number.isFinite(value) || value < 0)) throw new Error('Invalid outlet reading');
      if (!controller.signal.aborted) this.outlet.set(result);
    } catch {
      if (!controller.signal.aborted) this.outlet.set(null);
    } finally {
      if (this.outletRequest === controller) {
        this.outletRequest = null;
        this.checkingOutlet.set(false);
      }
    }
  }

  async refreshPairing() {
    if (!this.pairing || this.pairingRequest || !this.online() || document.hidden || !this.hostAvailable()) {
      if (this.pairing && (!this.online() || !this.hostAvailable())) this.pairingWaiting.set(null);
      return;
    }
    const controller = new AbortController();
    this.pairingRequest = controller;
    try {
      const response = await fetch('/api/control/moonshine/pairing', {
        cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
      });
      if (response.status === 401) { this.session.expired(); return; }
      if (!response.ok) throw new Error('Pairing status unavailable');
      const result: { waiting: boolean | null } = await response.json();
      this.pairingWaiting.set(result.waiting);
    } catch {
      this.pairingWaiting.set(null);
    } finally {
      if (this.pairingRequest === controller) this.pairingRequest = null;
    }
  }

  pairingLabel() {
    if (!this.online() || !this.hostAvailable()) return 'Unavailable';
    const waiting = this.pairingWaiting();
    return waiting ? 'Waiting for PIN' : waiting === false ? 'Idle' : 'Unknown';
  }

  setPin(input: HTMLInputElement) {
    // Keep only digits; write back so rejected characters never stay visible.
    input.value = input.value.replace(/\D/g, '').slice(0, 4);
    this.pin.set(input.value);
    this.pairingStatus.set(null);
  }

  canPair() { return !!this.pairing && !this.editing() && this.pin().length === 4 && !this.busy() && this.online() && this.hostAvailable(); }

  async pairMoonlight() {
    if (!this.canPair()) return;
    this.busy.set(true);
    this.pairingStatus.set({ severity: 'info', text: 'Sending PIN to Moonshine…' });
    try {
      const result = await api<{ message: string }>('/moonshine/pair', 'POST', { pin: this.pin() });
      this.pin.set('');
      this.pairingStatus.set({ severity: 'success', text: result.message });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) { this.session.expired(); return; }
      this.pairingStatus.set({ severity: 'error', text: error instanceof TypeError
        ? `Could not reach ${this.settings.title}.` : error instanceof Error ? error.message : 'Pairing failed' });
    } finally {
      this.busy.set(false);
      void this.refreshPairing();
    }
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
    this.nixosResponding.set(false);
    this.checkingPower.set(false);
  }

  powerLabel() {
    if (this.nixosResponding()) return 'Awake';
    const state = this.power()?.state;
    return state ? { awake: 'Awake', asleep: 'Asleep', off: 'Powered off', unknown: 'Unknown' }[state]
      : this.checkingPower() ? 'Checking…' : 'Unavailable';
  }

  osLabel() {
    if (this.nixosResponding()) return 'NixOS';
    if (this.power()?.state !== 'awake') return '';
    const state = this.power()?.operatingSystem?.state;
    return state === 'nixos' ? 'NixOS' : state === 'windows' ? 'Windows' : 'OS unknown';
  }

  hostLabel() {
    return this.powerLabel() + (this.osLabel() ? ` · ${this.osLabel()}` : '');
  }

  hostDetail() {
    const power = this.power();
    if (this.nixosResponding()) return 'NixOS confirmed by a fresh host API response. ' + (power?.detail ?? 'Checking motherboard power LED…');
    if (!power) return this.checkingPower() ? 'Reading power state through NanoKVM…' : 'NanoKVM status unavailable. Check Tailscale connectivity.';
    return power.detail + (power.state === 'awake' ? ' ' + (power.operatingSystem?.detail ?? 'OS detection unavailable.') : '');
  }

  canWake() {
    const state = this.power()?.state;
    return this.canWakeHost && !this.nixosResponding() && (state === 'asleep' || state === 'off');
  }

  canExecute(action: Action | null) {
    if (!action || this.busy() || !this.online() || this.editing()) return false;
    if (action.id === 'wake') return this.canWake();
    return this.hostAvailable();
  }

  hostAvailable() {
    return !this.hasWake || this.nixosResponding() || (this.power()?.state === 'awake'
      && this.power()?.operatingSystem?.state === 'nixos');
  }

  async refreshPower() {
    if (!this.hasWake || !this.settings.wakeUrl || this.powerRequest || !this.online() || document.hidden || this.busy()) return;
    const controller = new AbortController();
    this.powerRequest = controller;
    this.checkingPower.set(true);
    const direct = this.refreshNixOS(controller);
    try {
      const response = await fetch(new URL('status', this.settings.wakeUrl), {
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
      await direct;
      if (this.powerRequest === controller) {
        this.powerRequest = null;
        this.checkingPower.set(false);
      }
    }
  }

  private async refreshNixOS(controller: AbortController) {
    let confirmed = false;
    try {
      const response = await fetch('/api/control/os', {
        cache: 'no-store',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(2500)]),
      });
      if (response.status === 401) this.session.expired();
      confirmed = response.ok && (await response.json()).state === 'nixos';
    } catch { /* NanoKVM still detects Windows and unavailable host APIs. */ }
    if (!controller.signal.aborted) this.nixosResponding.set(confirmed);
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
      if (response.status === 401) this.session.expired();
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
    const state = this.devices()?.[device]?.state;
    if (!state) return this.checkingDevices() ? 'Checking…' : 'Unknown';
    return { on: 'On', off: 'Off', mixed: 'Mixed', dp: 'DP-1', hdmi: 'HDMI', both: 'Both', none: 'None',
      performance: 'Performance', balanced: 'Balanced', 'power-saver': 'Power saver', unknown: 'Unknown' }[state];
  }

  isCurrent(device: Device, action: Action) {
    const state = this.devices()?.[device]?.state;
    return device === 'screen' || device === 'profile' ? state === action.id.slice(device.length + 1)
      : state === (action.off ? 'off' : 'on');
  }

  deviceDetail(device: Device) {
    if (!this.online()) return 'Reconnect to refresh status.';
    return this.devices()?.[device]?.detail ?? (this.checkingDevices() ? 'Reading current state…' : 'Status unavailable.');
  }

  @HostListener('window:beforeinstallprompt', ['$event'])
  offerInstall(event: Event) {
    event.preventDefault();
    this.installPrompt.set(event as InstallPrompt);
  }

  @HostListener('window:appinstalled')
  appInstalled() { this.installPrompt.set(null); this.installed.set(true); }

  async install() {
    const prompt = this.installPrompt();
    this.installPrompt.set(null);
    await prompt?.prompt();
  }

  activate(action: Action) {
    if (!this.canExecute(action)) return;
    if (!action.confirm) { void this.execute(action); return; }
    this.confirmation.set(action);
    const ref = this.dialog.open(this.confirmDialog, { ariaLabelledBy: 'confirm-title', ariaDescribedBy: 'confirm-description', maxWidth: 'calc(100vw - 32px)' });
    ref.afterClosed().subscribe((confirmed: boolean) => {
      this.confirmation.set(null);
      if (confirmed) void this.execute(action);
    });
  }

  async execute(action: Action) {
    if (!this.canExecute(action)) return;
    if (action.id === 'wake') { await this.wake(); return; }
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
      if (response.status === 401) { this.session.expired(); return; }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Command failed');
      this.status.set({ severity: 'success', text: `${action.label}: command sent.` });
    } catch (error) {
      this.status.set({ severity: 'error', text: action.confirm
        ? `Connection lost or command failed. Check whether ${this.settings.title} is asleep, powered off, or rebooting.`
        : error instanceof Error ? error.message : 'Command failed' });
    } finally {
      this.busy.set(false);
      void this.refreshDevices();
      void this.refreshPower();
    }
  }

  async wake() {
    if (this.busy() || !navigator.onLine || !this.settings.wakeUrl || !this.canWake()) return;
    this.busy.set(true);
    this.clearDevices();
    this.clearPower();
    this.status.set({ severity: 'info', text: 'Checking power state through NanoKVM…' });
    try {
      const response = await fetch(this.settings.wakeUrl, {
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
              this.status.set({ severity: 'success', text: `${this.settings.title} is awake and its dashboard server is responding.` });
              if (this.session.cached()) void this.session.check();
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

  private visible(list: Arrangeable) {
    return this.order[list]().filter(id => this.editing() || this.layout().showHidden || !this.isHidden(list, id));
  }

  isHidden(list: Arrangeable, id: string) { return this.layout()[HIDDEN[list]].includes(id); }

  /** Rearranging needs a live server to save to (or no accounts at all). */
  canArrange() { return this.online() && !this.session.cached(); }

  toggleHidden(list: Arrangeable, id: string) {
    if (!this.canArrange()) return;
    const key = HIDDEN[list], hidden = this.layout()[key];
    this.saveLayout({ ...this.layout(), [key]: hidden.includes(id) ? hidden.filter(item => item !== id) : [...hidden, id] });
  }

  toggleEditing() {
    if (!this.editing() && !this.canArrange()) return;
    this.editing.update(editing => !editing);
  }

  setShowHidden(showHidden: boolean) { this.saveLayout({ ...this.layout(), showHidden }); }

  resetLayout() { this.saveLayout(emptyLayout()); }

  drop(list: Arrangeable, event: CdkDragDrop<unknown>) { this.move(list, event.previousIndex, event.currentIndex); }

  /** Keyboard alternative to dragging: arrow keys on the handle move the item. */
  nudge(list: Arrangeable, index: number, event: KeyboardEvent) {
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const target = index + step;
    if (target < 0 || target >= this.visible(list).length) return;
    this.move(list, index, target);
    // The handle is re-rendered at its new position; keep keyboard focus on it.
    const handle = event.target as HTMLElement;
    window.setTimeout(() => (handle.isConnected ? handle
      : document.querySelectorAll<HTMLElement>(`[data-list="${list}"] .drag-handle`)[target])?.focus());
  }

  private move(list: Arrangeable, from: number, to: number) {
    if (from === to || !this.canArrange()) return;
    // Reorder the shown items, leaving hidden ones in their slots of the full order.
    const shown = this.visible(list), moved = [...shown];
    moveItemInArray(moved, from, to);
    let next = 0;
    const full = this.order[list]().map(id => shown.includes(id) ? moved[next++] : id);
    this.saveLayout({ ...this.layout(), [list]: full });
  }

  private saveLayout(layout: Layout) {
    this.layout.set(layout);
    if (!this.settings.auth) {
      try { localStorage.setItem(LOCAL_LAYOUT_KEY, JSON.stringify(layout)); } catch { /* Not persisted. */ }
      return;
    }
    this.session.cacheLayout(layout);
    // Coalesce quick successive changes (e.g. keyboard moves) into one request.
    window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => void this.flushLayout(), 400);
  }

  private async flushLayout() {
    if (this.saveTimer === undefined) return;
    window.clearTimeout(this.saveTimer);
    this.saveTimer = undefined;
    try {
      await api('/layout', 'PUT', this.layout());
    } catch (error) {
        if (error instanceof ApiError && error.status === 401) { this.session.expired(); return; }
      this.status.set({ severity: 'error', text: 'Could not save your layout. It will reset when you reload.' });
    }
  }

  changePassword() {
    this.dialog.open(PasswordDialog, { maxWidth: 'calc(100vw - 32px)', width: '420px' });
  }
}
