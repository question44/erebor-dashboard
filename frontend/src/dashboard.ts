import { Component, DestroyRef, ElementRef, HostListener, TemplateRef, ViewChild, computed, effect, inject, signal } from '@angular/core';
import { CdkDrag, CdkDragDrop, CdkDragHandle, CdkDropList, moveItemInArray } from '@angular/cdk/drag-drop';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { A11yModule } from '@angular/cdk/a11y';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatChipsModule } from '@angular/material/chips';
import { MatMenuModule } from '@angular/material/menu';
import { Action, ApiError, Layout, Section, Settings, api, availableActions, emptyLayout, resolveServices } from './config';
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
type Arrangeable = Section;
const HIDDEN = { cards: 'hiddenCards', services: 'hiddenServices' } as const;
// Layout for dashboards without accounts, where there is no server-side profile.
const LOCAL_LAYOUT_KEY = 'erebor-dashboard-layout';

/** Saved IDs first (dropping unknown ones), then anything new in its default position. */
const arrange = (ids: string[], saved: string[]) => [...new Set([...saved.filter(id => ids.includes(id) || isSpacer(id)), ...ids])];
// Empty one-unit cells a user places to leave gaps; stored in the order lists like any item.
const SPACER = 'spacer-';
// Spring-like easing with a small overshoot that settles into place (falls back to cubic-bezier where linear() is unsupported).
const SPRING = CSS.supports('transition-timing-function', 'linear(0, 1)')
  ? 'linear(0, 0.06 2%, 0.25 6%, 0.55 12%, 0.83 19%, 1 25%, 1.05 31%, 1.06 36%, 1.04 44%, 1.01 55%, 0.995 66%, 1)'
  : 'cubic-bezier(.2, .9, .3, 1.1)';
const MAX_WINDOWS = 8;
interface EmbeddableService { id: string; label: string; url?: string; embedOn?: string[]; }
interface EmbedWindow {
  key: number; number: number; label: string; service: EmbeddableService; src: SafeResourceUrl;
  z: number; offset: number; maximized: boolean; minimized: boolean;
}
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const MAX_SPACERS = 16;
export const isSpacer = (id: string) => id.startsWith(SPACER);

function readLocalLayout(): Layout {
  try { return { ...emptyLayout(), ...JSON.parse(localStorage.getItem(LOCAL_LAYOUT_KEY) ?? '{}') }; } catch { return emptyLayout(); }
}

@Component({
  selector: 'erebor-dashboard',
  imports: [Icon, A11yModule, MatButtonModule, MatDialogModule, MatChipsModule, MatMenuModule, MatSlideToggleModule,
    CdkDropList, CdkDrag, CdkDragHandle],
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
  readonly actionById = Object.fromEntries(this.systemActions.map(action => [action.id, action]));
  readonly cardIds = [...this.deviceControls.map(device => device.id), ...(this.pairing ? ['moonshine-pair'] : []),
    ...this.systemActions.map(action => action.id)];
  readonly isSpacer = isSpacer;
  readonly serviceById = Object.fromEntries(this.services.map(service => [service.id, service]));
  readonly layout = signal<Layout>({ ...emptyLayout(), ...(this.settings.auth ? this.settings.layout : readLocalLayout()) });
  private readonly order = {
    cards: computed(() => arrange(this.cardIds, this.layout().cards)),
    services: computed(() => arrange(this.services.map(service => service.id), this.layout().services)),
  };
  readonly visibleCards = computed(() => this.visible('cards'));
  readonly visibleServices = computed(() => this.visible('services'));
  readonly hasVisibleService = computed(() => this.visibleServices().some(id => !isSpacer(id)));
  readonly hiddenCount = computed(() => this.order.cards().filter(id => this.isHidden('cards', id)).length
    + this.order.services().filter(id => this.isHidden('services', id)).length);
  private saveTimer?: number;
  /** Edit mode: items wiggle, can be dragged and hidden; their own controls are paused. */
  readonly editing = signal(false);
  // Touch needs a short hold so a quick swipe still scrolls the page.
  readonly dragDelay = { touch: 180, mouse: 0 };
  /** Service shown in the floating in-page window (e.g. the browser terminal). */
  /** Floating in-page windows for embedded services (e.g. several browser terminals). */
  readonly windows = signal<EmbedWindow[]>([]);
  private windowSeq = 0;
  private zSeq = 0;
  /** True while the window is moved or resized, so the iframe does not swallow pointer events. */
  readonly embedInteracting = signal(false);
  private readonly sanitizer = inject(DomSanitizer);
  /** Bottom edge (px from the grid top) of each rendered row, measured from the DOM. */
  readonly rowBottoms = { cards: signal<number[]>([]), services: signal<number[]>([]) };
  /** Pointer position of the fold line while it is being dragged in edit mode. */
  readonly foldDrag = signal<{ list: Section; y: number } | null>(null);
  private readonly grids: Partial<Record<Section, HTMLElement>> = {};
  private readonly observers: Partial<Record<Section, { resize: ResizeObserver; mutation: MutationObserver }>> = {};
  /** Last laid-out position of each grid item, for animating moves (FLIP). */
  private readonly positions: Record<Section, Map<Element, [number, number]>> = { cards: new Map(), services: new Map() };
  private readonly flips = new WeakMap<Element, Animation>();
  private readonly wasInert = new WeakMap<Element, boolean>();
  /** CDK puts the DOM back before Angular re-renders a drop; neither step should animate. */
  private skipFlip = false;
  @ViewChild('cardsGrid') set cardsGrid(ref: ElementRef<HTMLElement> | undefined) { this.observeGrid('cards', ref?.nativeElement); }
  @ViewChild('servicesGrid') set servicesGrid(ref: ElementRef<HTMLElement> | undefined) { this.observeGrid('services', ref?.nativeElement); }
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
    // Items below the fold are out of reach for keyboard and screen readers too.
    effect(() => {
      for (const list of ['cards', 'services'] as const) {
        const grid = this.grids[list], cut = this.clipHeight(list);
        this.rowBottoms[list]();
        if (!grid) continue;
        let revealed = 0;
        for (const child of Array.from(grid.children) as HTMLElement[]) {
          const inert = cut !== null && child.offsetTop >= cut;
          // Rows uncovered by unfolding slide in one after another.
          if (this.wasInert.get(child) && !inert && !reducedMotion())
            child.animate([{ opacity: 0, translate: '0 -14px', scale: '.97' }, { opacity: 1, translate: '0 0', scale: '1' }],
              { duration: 480, easing: SPRING, delay: Math.min(240, 40 * revealed++), fill: 'backwards' });
          this.wasInert.set(child, inert);
          child.toggleAttribute('inert', inert);
        }
      }
    });
    inject(DestroyRef).onDestroy(() => {
      this.flushLayout();
      for (const list of ['cards', 'services'] as const) this.observeGrid(list, undefined);
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

  private observeGrid(list: Section, grid: HTMLElement | undefined) {
    if (this.grids[list] === grid) return;
    this.observers[list]?.resize.disconnect();
    this.observers[list]?.mutation.disconnect();
    delete this.observers[list];
    this.grids[list] = grid;
    if (!grid) { this.rowBottoms[list].set([]); return; }
    const resize = new ResizeObserver(() => { this.snapshot(list); this.measureRows(list); });
    const mutation = new MutationObserver(() => { this.flip(list); this.measureRows(list); });
    resize.observe(grid);
    mutation.observe(grid, { childList: true });
    this.observers[list] = { resize, mutation };
    this.snapshot(list);
    this.measureRows(list);
  }

  private snapshot(list: Section) {
    const grid = this.grids[list];
    this.positions[list] = new Map((Array.from(grid?.children ?? []) as HTMLElement[])
      .map(child => [child, [child.offsetLeft, child.offsetTop]]));
  }

  /** Animate items whose grid position changed from where they were to where they are now. */
  private flip(list: Section) {
    const before = this.positions[list];
    this.snapshot(list);
    if (this.skipFlip || reducedMotion()) return;
    const dragging = !!this.grids[list]?.classList.contains('cdk-drop-list-dragging');
    for (const [element, [x, y]] of this.positions[list]) {
      const old = before.get(element);
      if (!old) continue;
      // Start from where the item is drawn now, including any move still in progress.
      const [tx, ty] = (getComputedStyle(element).translate.match(/-?[\d.]+/g) ?? ['0', '0']).map(Number);
      const dx = old[0] - x + (tx || 0), dy = old[1] - y + (ty || 0);
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      this.flips.get(element)?.cancel();
      // Shorter while dragging: a sliding item ignores the pointer until it settles.
      const animation = element.animate([{ translate: `${dx}px ${dy}px` }, { translate: '0 0' }],
        { duration: dragging ? 250 : 420, easing: SPRING });
      this.flips.set(element, animation);
      // CDK hit-tests with elementFromPoint: while dragging, a sliding item must not count as
      // "under the pointer" or it would be swapped straight back.
      if (dragging) {
        (element as HTMLElement).style.pointerEvents = 'none';
        const settle = () => { if (this.flips.get(element) === animation) (element as HTMLElement).style.pointerEvents = ''; };
        animation.onfinish = animation.oncancel = settle;
      }
    }
  }

  private measureRows(list: Section) {
    const grid = this.grids[list];
    if (!grid) return;
    // Group items by their top edge; a row ends at its tallest item.
    const rows = new Map<number, number>();
    for (const child of Array.from(grid.children) as HTMLElement[]) {
      if (child.classList.contains('cdk-drag-placeholder') || !child.offsetHeight) continue;
      const top = Math.round(child.offsetTop);
      rows.set(top, Math.max(rows.get(top) ?? 0, top + child.offsetHeight));
    }
    const bottoms = [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([, bottom]) => bottom);
    if (JSON.stringify(bottoms) !== JSON.stringify(this.rowBottoms[list]())) this.rowBottoms[list].set(bottoms);
  }

  foldRows(list: Section) {
    return Math.max(1, Math.min(this.layout().foldRows[list] ?? 1, this.rowBottoms[list]().length || 1));
  }

  isFolded(list: Section) { return this.layout().folded.includes(list); }

  /** Whether folding would hide anything in this section. */
  canFold(list: Section) { return this.rowBottoms[list]().length > this.foldRows(list); }

  /** Max height of the section while folded; null when everything is shown (always in edit mode). */
  clipHeight(list: Section): number | null {
    if (this.editing() || !this.isFolded(list) || !this.canFold(list)) return null;
    return this.rowBottoms[list]()[this.foldRows(list) - 1];
  }

  /** Height the section animates to: the fold, or its full height; null (unclipped) while editing. */
  maxHeight(list: Section): number | null {
    const bottoms = this.rowBottoms[list]();
    if (this.editing() || !bottoms.length) return null;
    return this.clipHeight(list) ?? bottoms[bottoms.length - 1];
  }

  /** Clicking anywhere on a section's heading row folds or unfolds it. */
  headingClick(list: Section) { if (!this.editing() && (this.canFold(list) || this.isFolded(list))) this.toggleFold(list); }

  hiddenRows(list: Section) { return this.rowBottoms[list]().length - this.foldRows(list); }

  toggleFold(list: Section) {
    const folded = this.layout().folded;
    this.saveLayout({ ...this.layout(), folded: folded.includes(list) ? folded.filter(s => s !== list) : [...folded, list] });
  }

  /** Where the fold line sits in edit mode: under row N, or under the pointer while dragging. */
  foldLineTop(list: Section) {
    const drag = this.foldDrag();
    if (drag?.list === list) return drag.y;
    const bottoms = this.rowBottoms[list](), n = this.foldRows(list);
    return bottoms[n - 1] + (n < bottoms.length ? (this.rowTop(list, n) - bottoms[n - 1]) / 2 : 6);
  }

  private rowTop(list: Section, index: number) {
    const grid = this.grids[list];
    const tops = [...new Set((Array.from(grid?.children ?? []) as HTMLElement[]).filter(c => c.offsetHeight)
      .map(c => Math.round(c.offsetTop)))].sort((a, b) => a - b);
    return tops[index] ?? 0;
  }

  /** Row count shown on the fold grip: the snap target while dragging. */
  foldLabel(list: Section) {
    const drag = this.foldDrag();
    return drag?.list === list ? this.nearestRows(list, drag.y) : this.foldRows(list);
  }

  /** Row count the fold line snaps to for a pointer position. */
  private nearestRows(list: Section, y: number) {
    const bottoms = this.rowBottoms[list]();
    let best = 1;
    bottoms.forEach((bottom, i) => { if (Math.abs(bottom - y) < Math.abs(bottoms[best - 1] - y)) best = i + 1; });
    return best;
  }

  startFoldDrag(list: Section, event: PointerEvent) {
    const grid = this.grids[list];
    if (!grid || !this.canArrange()) return;
    event.preventDefault();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);
    const top = grid.getBoundingClientRect().top, max = grid.offsetHeight;
    const move = (e: PointerEvent) => this.foldDrag.set({ list, y: Math.max(0, Math.min(max, e.clientY - top)) });
    const end = (e: PointerEvent) => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
      const y = this.foldDrag()?.y;
      this.foldDrag.set(null);
      if (y !== undefined && e.type === 'pointerup') this.setFoldRows(list, this.nearestRows(list, y));
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
    move(event);
  }

  nudgeFold(list: Section, event: KeyboardEvent) {
    const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    this.setFoldRows(list, Math.max(1, Math.min(this.rowBottoms[list]().length, this.foldRows(list) + step)));
  }

  private setFoldRows(list: Section, rows: number) {
    if (rows === (this.layout().foldRows[list] ?? 1)) return;
    this.saveLayout({ ...this.layout(), foldRows: { ...this.layout().foldRows, [list]: rows } });
  }

  /** Only configured HTTPS services, and only on the dashboard hosts they list. */
  canEmbed(service: { url?: string; embedOn?: string[] }) {
    return !!service.url?.startsWith('https://') && !!service.embedOn?.includes(location.hostname);
  }

  openService(service: EmbeddableService, event: MouseEvent) {
    // Modified or middle clicks keep their usual new-tab/window behaviour.
    if (!this.canEmbed(service) || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    this.openWindow(service);
  }

  /** Each window is its own iframe, so ttyd starts a separate shell for it. */
  openWindow(service: EmbeddableService) {
    const open = this.windows();
    if (open.length >= MAX_WINDOWS) {
      this.focusWindow(open.reduce((a, b) => (a.z > b.z ? a : b)).key);
      return;
    }
    const used = new Set(open.filter(w => w.service.id === service.id).map(w => w.number));
    let number = 1;
    while (used.has(number)) number++;
    const key = ++this.windowSeq;
    // Cascade new windows so they don't sit exactly on top of each other.
    const offset = 32 * (open.length % 6);
    this.windows.update(list => [...list, { key, number, service, z: ++this.zSeq, maximized: false, minimized: false, offset,
      label: number === 1 ? service.label : `${service.label} ${number}`,
      src: this.sanitizer.bypassSecurityTrustResourceUrl(service.url!) }]);
    window.setTimeout(() => this.focusFrame(key));
  }

  private updateWindow(key: number, change: Partial<EmbedWindow>) {
    this.windows.update(list => list.map(w => (w.key === key ? { ...w, ...change } : w)));
  }

  /** The frontmost visible window; it gets the active styling and dock highlight. */
  readonly topWindow = computed(() => this.windows().filter(w => !w.minimized).reduce<EmbedWindow | null>(
    (top, w) => (!top || w.z > top.z ? w : top), null)?.key ?? null);

  focusWindow(key: number, focusFrame = true) {
    const win = this.windows().find(w => w.key === key);
    if (!win) return;
    if (win.z !== this.zSeq || win.minimized) this.updateWindow(key, { z: ++this.zSeq, minimized: false });
    if (focusFrame) window.setTimeout(() => this.focusFrame(key));
  }

  private focusFrame(key: number) {
    document.querySelector<HTMLIFrameElement>(`.embed-window[data-key="${key}"] iframe`)?.focus();
  }

  closeWindow(key: number) { this.windows.update(list => list.filter(w => w.key !== key)); }

  toggleMaximized(key: number) {
    const win = this.windows().find(w => w.key === key);
    if (win) this.updateWindow(key, { maximized: !win.maximized });
  }

  minimizeWindow(key: number) { this.updateWindow(key, { minimized: true }); }

  /** Dock chip: restore a minimised window, minimise the front one, otherwise bring it forward. */
  dockClick(key: number) {
    const win = this.windows().find(w => w.key === key);
    if (!win) return;
    if (!win.minimized && this.topWindow() === key) this.minimizeWindow(key);
    else this.focusWindow(key);
  }

  /** Clicks inside an iframe never reach this page; the page losing focus to one of them means it was clicked. */
  @HostListener('window:blur')
  frameFocused() {
    window.setTimeout(() => {
      const frame = document.activeElement;
      const key = frame instanceof HTMLIFrameElement ? Number(frame.closest<HTMLElement>('.embed-window')?.dataset['key']) : NaN;
      if (key) this.focusWindow(key, false);
    });
  }

  /** Pointer down on the window frame (its resize corner) rather than the iframe content. */
  embedPointerDown(key: number, event: PointerEvent) {
    this.focusWindow(key, false);
    if (event.target !== event.currentTarget) return;
    this.embedInteracting.set(true);
    window.addEventListener('pointerup', () => this.embedInteracting.set(false), { once: true });
  }

  cardKind(id: string) {
    return isSpacer(id) ? 'spacer' : id === 'moonshine-pair' ? 'pair' : this.actionById[id] ? 'action' : 'device';
  }

  spacerCount(list: Arrangeable) { return this.order[list]().filter(isSpacer).length; }

  addSpacer(list: Arrangeable) {
    if (!this.canArrange() || this.spacerCount(list) >= MAX_SPACERS) return;
    const id = SPACER + Math.random().toString(36).slice(2, 10);
    this.saveLayout({ ...this.layout(), [list]: [...this.order[list](), id] });
  }

  removeSpacer(list: Arrangeable, id: string) {
    if (!this.canArrange()) return;
    this.saveLayout({ ...this.layout(), [list]: this.order[list]().filter(item => item !== id) });
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

  drop(list: Arrangeable, event: CdkDragDrop<unknown>) {
    // Items already slid into place while dragging; don't replay the DOM reset and re-render.
    this.skipFlip = true;
    requestAnimationFrame(() => requestAnimationFrame(() => { this.skipFlip = false; this.snapshot(list); }));
    this.move(list, event.previousIndex, event.currentIndex);
  }

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
