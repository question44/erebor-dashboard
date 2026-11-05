import { Injectable, signal } from '@angular/core';
import { Account, ApiError, PublicSettings, Settings, api } from './config';

export type View =
  | { kind: 'loading' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'setup' }
  | { kind: 'login'; message?: string }
  | { kind: 'dashboard' }
  | { kind: 'users' };

interface SessionState { setupRequired: boolean; user: Account | null; }

// The last signed-in view, so the installed app still offers Wake while the host sleeps.
const CACHE_KEY = 'erebor-dashboard-view';
// Accounts that signed in on this device, newest first; never shared with the server.
const RECENT_KEY = 'erebor-dashboard-recent-accounts';
const RECENT_LIMIT = 5;

export interface RecentAccount { username: string; signedIn: number; }

@Injectable({ providedIn: 'root' })
export class Session {
  readonly view = signal<View>({ kind: 'loading' });
  readonly settings = signal<Settings | null>(null);
  /** True while showing the cached view because the server could not be reached. */
  readonly cached = signal(false);
  /** Accounts recently signed in on this browser, shown on the sign-in screen. */
  readonly recent = signal<RecentAccount[]>(this.readRecent());
  private publicSettings!: PublicSettings;
  private retry?: number;

  get branding() { return this.publicSettings; }
  get account(): Account | null { return this.settings()?.user ?? null; }

  async start(publicSettings: PublicSettings) {
    this.publicSettings = publicSettings;
    if (!publicSettings.auth) {
      // No control API accounts: the public configuration is the whole dashboard.
      this.settings.set(publicSettings as Settings);
      this.view.set({ kind: 'dashboard' });
      return;
    }
    await this.check();
  }

  /** Ask the server who we are; fall back to the cached view only when it is unreachable. */
  async check() {
    window.clearTimeout(this.retry);
    try {
      const state = await api<SessionState>('/auth/session', 'GET', undefined, 5000);
      if (state.setupRequired) { this.forget(); this.view.set({ kind: 'setup' }); return; }
      if (!state.user) { this.forget(); this.view.set({ kind: 'login' }); return; }
      await this.loadDashboard();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) { this.expired(); return; }
      const cached = this.readCache();
      if (cached) {
        this.cached.set(true);
        if (this.settings() === null || this.view().kind === 'loading' || this.view().kind === 'unavailable') {
          this.settings.set(cached);
          this.view.set({ kind: 'dashboard' });
        }
      } else {
        this.view.set({ kind: 'unavailable', message: error instanceof ApiError && error.status === 403
          ? error.message : `Could not reach ${this.publicSettings.title}. It may be asleep, powered off, or running another operating system.` });
      }
      this.retry = window.setTimeout(() => void this.check(), 15000);
    }
  }

  async loadDashboard() {
    const settings = await api<Settings>('/config');
    const changed = JSON.stringify(settings) !== JSON.stringify(this.settings());
    localStorage.setItem(CACHE_KEY, JSON.stringify(settings));
    this.cached.set(false);
    // Avoid recreating the dashboard (and its polling) when nothing changed.
    if (changed || this.view().kind !== 'dashboard') {
      this.settings.set(settings);
      if (this.view().kind !== 'users') this.view.set({ kind: 'dashboard' });
    }
  }

  async setup(username: string, password: string) {
    await api<SessionState>('/auth/setup', 'POST', { username, password });
    this.remember(username);
    await this.loadDashboard();
  }

  async login(username: string, password: string) {
    await api<SessionState>('/auth/login', 'POST', { username, password });
    this.remember(username);
    await this.loadDashboard();
  }

  async logout() {
    try { await api('/auth/logout', 'POST', {}); } catch { /* The local session is cleared regardless. */ }
    this.forget();
    this.view.set({ kind: 'login', message: 'Signed out.' });
  }

  /** Called on any 401: the session ended or the account was removed. */
  expired() {
    if (!this.publicSettings?.auth) return;
    this.forget();
    this.view.set({ kind: 'login', message: 'Your session ended. Sign in again.' });
  }

  show(kind: 'dashboard' | 'users') { this.view.set({ kind }); }

  private forget() {
    localStorage.removeItem(CACHE_KEY);
    this.settings.set(null);
    this.cached.set(false);
  }

  forgetRecent(username: string) {
    this.saveRecent(this.recent().filter(account => account.username !== username));
  }

  private remember(username: string) {
    this.saveRecent([{ username, signedIn: Date.now() },
      ...this.recent().filter(account => account.username !== username)].slice(0, RECENT_LIMIT));
  }

  private saveRecent(accounts: RecentAccount[]) {
    this.recent.set(accounts);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(accounts)); } catch { /* Storage may be unavailable. */ }
  }

  private readRecent(): RecentAccount[] {
    try {
      const value = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
      return Array.isArray(value) ? value.filter(account => typeof account?.username === 'string'
        && typeof account.signedIn === 'number').slice(0, RECENT_LIMIT) : [];
    } catch { return []; }
  }

  private readCache(): Settings | null {
    try {
      const value = JSON.parse(localStorage.getItem(CACHE_KEY) ?? 'null');
      return value && Array.isArray(value.actions) && Array.isArray(value.services) && value.user ? value : null;
    } catch { return null; }
  }
}
