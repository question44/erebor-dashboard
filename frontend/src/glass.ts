import { signal } from '@angular/core';

// Per-device (per-browser) choice: let the window behind show through the page
// background. Only browsers that composite pages over a translucent window can
// show it (e.g. Zen with browser.tabs.allow_transparent_browser), so it is off
// unless switched on here.
const GLASS_KEY = 'erebor-dashboard-glass';
const read = () => { try { return localStorage.getItem(GLASS_KEY) === '1'; } catch { return false; } };

export const glass = signal(read());

function apply() { document.documentElement.classList.toggle('glass', glass()); }

export function setGlass(on: boolean) {
  glass.set(on);
  try { localStorage.setItem(GLASS_KEY, on ? '1' : '0'); } catch { /* Per-session only. */ }
  apply();
}

// Applied at import, before the first view renders (main.ts imports this first).
apply();
