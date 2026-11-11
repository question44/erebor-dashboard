import { signal } from '@angular/core';
import { SequoiaRenderer, loadSequoiaData } from './sequoia';

// Per-device (per-browser) page background, chosen in the account menu:
// - solid: the theme's page colour.
// - transparent: no page colour, so the window behind shows through. Only
//   browsers that composite pages over a translucent window show it (e.g. Zen
//   with browser.tabs.allow_transparent_browser).
// - sequoia / sequoia-still: macOS Sequoia's wallpaper with liquid glass under
//   the cards (sequoia.ts); needs a signed-in session and settings.sequoia.
export type Background = 'solid' | 'transparent' | 'sequoia' | 'sequoia-still';
export const BACKGROUNDS: { id: Background; label: string }[] = [
  { id: 'solid', label: 'Solid' }, { id: 'transparent', label: 'Transparent' },
  { id: 'sequoia', label: 'Sequoia' }, { id: 'sequoia-still', label: 'Sequoia (still)' },
];
const KEY = 'erebor-dashboard-background';
const OLD_GLASS_KEY = 'erebor-dashboard-glass'; // the earlier on/off "Transparent background"

function read(): Background {
  try {
    const stored = localStorage.getItem(KEY);
    if (BACKGROUNDS.some(b => b.id === stored)) return stored as Background;
    return localStorage.getItem(OLD_GLASS_KEY) === '1' ? 'transparent' : 'solid';
  } catch { return 'solid'; }
}

export const background = signal<Background>(read());
let renderer: SequoiaRenderer | null = null;
let starting: Promise<void> | null = null;
let available = false;

const isSequoia = (b: Background) => b === 'sequoia' || b === 'sequoia-still';

function apply() {
  const b = background(), root = document.documentElement;
  root.classList.toggle('glass', b === 'transparent');
  root.classList.toggle('sequoia', isSequoia(b) && renderer !== null);
  if (isSequoia(b)) { if (available) void start(); } else stop();
}

function stop() {
  renderer?.stop(); renderer = null;
  document.querySelector('canvas.sequoia-canvas')?.remove();
}

async function start() {
  if (renderer || starting) return starting ?? undefined;
  starting = (async () => {
    try {
      const data = await loadSequoiaData();
      if (!isSequoia(background()) || renderer) return;
      const canvas = document.createElement('canvas');
      canvas.className = 'sequoia-canvas';
      canvas.setAttribute('aria-hidden', 'true');
      document.body.prepend(canvas);
      try {
        renderer = new SequoiaRenderer(canvas, data, () => background() === 'sequoia-still');
      } catch (error) { canvas.remove(); throw error; }
    } catch (error) {
      console.error('Sequoia background unavailable:', error);
    } finally {
      starting = null;
      document.documentElement.classList.toggle('sequoia', isSequoia(background()) && renderer !== null);
    }
  })();
  return starting;
}

export function setBackground(b: Background) {
  background.set(b);
  try { localStorage.setItem(KEY, b); localStorage.removeItem(OLD_GLASS_KEY); } catch { /* Per-session only. */ }
  apply();
}

/** Called once the signed-in configuration says whether the Sequoia data exists. */
export function sequoiaAvailable(on: boolean) {
  available = on;
  apply();
}

// Applied at import, before the first view renders (main.ts imports this first).
apply();
