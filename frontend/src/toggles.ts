// Liquid glass controls, as liquidglass.dev builds them (vendor/liquid-glass.js,
// MIT). The lens bends a copy of the control's own content through an SVG
// filter on that copy (filter: url(), which every engine renders, unlike
// backdrop-filter: url()), so it works in Zen/Firefox too and in every
// background mode.
//
// - Device toggle rows (.device-actions): a segmented control. A copy of the
//   option labels in the accent colour on a light surface sits over the row,
//   clipped to a lens that springs to the current option and stretches with
//   its speed (liquidglass.dev's <lg-segmented>). A press moves it at once;
//   the API confirms a moment later, or it springs back.
// - The Show hidden switch (.glass-switch): a white knob that, while pressed,
//   swells into a clear lens magnifying the track (<lg-switch>).

import { createGlass, createSpring, type Glass, type Spring } from './vendor/liquid-glass.js';

const ROW = '.device-actions';
const OPTION = '.device-action';
const CURRENT = '.device-action.current';
const OPTIMISTIC_MS = 2500;

// ---- segmented rows ----------------------------------------------------------
interface Row {
  row: HTMLElement; copy: HTMLElement; shadow: HTMLElement; glass: Glass | null;
  spans: Map<HTMLElement, HTMLElement>;
  x: number; y: number; w: number; h: number; vx: number; vy: number;
  xs: Spring; ys: Spring; ws: Spring; hs: Spring;
  pending: HTMLElement | null; pendingUntil: number; placed: boolean; radius: number;
  last: HTMLElement | null; // the option the lens last sat on
}
const rows = new Map<HTMLElement, Row>();

function addRow(row: HTMLElement): Row {
  const copy = document.createElement('span');
  copy.className = 'seg-copy'; copy.setAttribute('aria-hidden', 'true');
  const shadow = document.createElement('span');
  shadow.className = 'seg-shadow'; shadow.setAttribute('aria-hidden', 'true');
  row.append(shadow, copy);
  const r = { row, copy, shadow, glass: null, spans: new Map(), x: 0, y: 0, w: 0, h: 0, vx: 0, vy: 0,
    pending: null, pendingUntil: 0, placed: false, radius: 12, last: null } as unknown as Row;
  const opts = { stiffness: 380, damping: 28 };
  r.xs = createSpring(0, (v, vel) => { r.x = v; r.vx = vel; renderRow(r); }, opts);
  r.ys = createSpring(0, (v, vel) => { r.y = v; r.vy = vel; renderRow(r); }, opts);
  r.ws = createSpring(0, v => { r.w = v; renderRow(r); }, opts);
  r.hs = createSpring(0, v => { r.h = v; renderRow(r); }, opts);
  rows.set(row, r);
  return r;
}

/** Mirror each option's label at its position, in the copy the lens bends. */
function layoutCopy(r: Row) {
  const options = [...r.row.querySelectorAll<HTMLElement>(OPTION)];
  for (const [b, span] of r.spans) if (!options.includes(b)) { span.remove(); r.spans.delete(b); }
  for (const b of options) {
    let span = r.spans.get(b);
    if (!span) { span = document.createElement('span'); span.className = 'seg-copy__option'; r.copy.append(span); r.spans.set(b, span); }
    const label = b.textContent?.trim() ?? '';
    if (span.textContent !== label) span.textContent = label;
    const s = span.style;
    s.left = `${b.offsetLeft}px`; s.top = `${b.offsetTop}px`; s.width = `${b.offsetWidth}px`; s.height = `${b.offsetHeight}px`;
  }
  const first = options[0];
  if (first) r.radius = parseFloat(getComputedStyle(first).borderTopLeftRadius) || 12;
}

// While a command runs the dashboard clears every device's state until fresh
// status arrives, so no option is current for a moment: the lens then stays
// where it was (no flicker on the other rows, and a real slide once the new
// state lands) instead of disappearing.
function targetOf(r: Row): HTMLElement | null {
  const current = r.row.querySelector<HTMLElement>(CURRENT);
  if (r.pending && (performance.now() > r.pendingUntil || r.pending === current || !r.pending.isConnected)) r.pending = null;
  if (r.last && !r.last.isConnected) r.last = null;
  const target = r.pending ?? current ?? r.last;
  if (target) r.last = target;
  return target;
}

function moveRow(r: Row, immediate = false) {
  const t = targetOf(r);
  r.row.classList.toggle('has-lens', !!t);
  if (!t) { r.placed = false; return; }
  const now = immediate || !r.placed;
  r.xs.set(t.offsetLeft, { immediate: now }); r.ys.set(t.offsetTop, { immediate: now });
  r.ws.set(t.offsetWidth, { immediate: now }); r.hs.set(t.offsetHeight, { immediate: now });
  r.placed = true;
}

function renderRow(r: Row) {
  if (!r.w) return;
  if (!r.glass && r.copy.offsetWidth) {
    r.glass = createGlass(r.copy, { clip: true, refraction: 5, bezel: 0.35, curvature: 4, chroma: 0.2, specular: 0.6, specularWidth: 1.5 });
  }
  const speed = Math.hypot(r.vx, r.vy);
  const stretch = Math.min(12, speed * 0.02);
  const horizontal = Math.abs(r.vx) >= Math.abs(r.vy);
  const w = r.w + (horizontal ? stretch : -stretch * 0.25);
  const h = r.h + (horizontal ? -stretch * 0.25 : stretch);
  const left = r.x - (w - r.w) / 2, top = r.y - (h - r.h) / 2;
  r.glass?.update({ x: left, y: top, width: w, height: h, radius: Math.min(r.radius, h / 2) });
  const s = r.shadow.style;
  s.transform = `translate(${left}px, ${top}px)`; s.width = `${w}px`; s.height = `${h}px`; s.borderRadius = `${Math.min(r.radius, h / 2)}px`;
}

// ---- switches ----------------------------------------------------------------
interface Switch { control: HTMLElement; input: HTMLInputElement; glass: Glass | null; x: number; v: number; p: number; xs: Spring; on: boolean; }
const switches = new Map<HTMLElement, Switch>();

function addSwitch(control: HTMLElement): Switch | null {
  const input = control.querySelector<HTMLInputElement>('input');
  if (!input) return null;
  const sw = { control, input, glass: null, x: input.checked ? 1 : 0, v: 0, p: 0, on: input.checked } as unknown as Switch;
  sw.xs = createSpring(sw.x, (val, vel) => { sw.x = val; sw.v = vel; renderSwitch(sw); }, { stiffness: 420, damping: 30 });
  const press = createSpring(0, val => { sw.p = val; renderSwitch(sw); }, { stiffness: 600, damping: 36 });
  input.addEventListener('pointerdown', () => press.set(1));
  for (const type of ['pointerup', 'pointercancel', 'pointerleave']) input.addEventListener(type, () => press.set(0));
  input.addEventListener('change', () => syncSwitch(sw));
  switches.set(control, sw);
  return sw;
}

function syncSwitch(sw: Switch) {
  if (sw.input.checked !== sw.on) { sw.on = sw.input.checked; sw.xs.set(sw.on ? 1 : 0); }
  renderSwitch(sw);
}

// liquidglass.dev <lg-switch> geometry: a 64 x 28 track, the knob 36 x 24 at rest, 54 x 36 pressed.
function renderSwitch(sw: Switch) {
  const refract = sw.control.querySelector<HTMLElement>('.gs-refract');
  const thumb = sw.control.querySelector<HTMLElement>('.gs-thumb');
  const shadow = sw.control.querySelector<HTMLElement>('.gs-shadow');
  if (!refract || !thumb || !shadow) return;
  if (!sw.glass && refract.offsetWidth) {
    sw.glass = createGlass(refract, { clip: true, refraction: 10, bezel: 0.9, curvature: 3, chroma: 0.2, specular: 0.7, specularWidth: 1.5 });
  }
  const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
  const stretch = Math.min(10, Math.abs(sw.v) * 1.6);
  const w = lerp(36, 54, sw.p) + stretch, h = lerp(24, 36, sw.p) - stretch * 0.2;
  const left = 20 + 24 * sw.x - w / 2, top = 14 - h / 2;
  sw.control.style.setProperty('--p', sw.x.toFixed(3));
  sw.glass?.update({ x: left + 16, y: top + 16, width: w, height: h, radius: h / 2 });
  for (const el of [thumb, shadow]) {
    const s = el.style;
    s.transform = `translate(${left}px, ${top}px)`; s.width = `${w}px`; s.height = `${h}px`; s.borderRadius = `${h / 2}px`;
  }
  thumb.style.opacity = String(1 - sw.p);
  shadow.style.opacity = String(sw.p);
}

// ---- wiring --------------------------------------------------------------------
let scanning = false;
function scan() {
  if (scanning) return;
  scanning = true;
  requestAnimationFrame(() => {
    scanning = false;
    for (const [row, r] of rows) {
      if (!row.isConnected) { r.glass?.destroy(); rows.delete(row); }
    }
    for (const row of document.querySelectorAll<HTMLElement>(ROW)) {
      if (!row.querySelector(CURRENT) && !rows.has(row)) continue;
      const r = rows.get(row) ?? addRow(row);
      layoutCopy(r);
      moveRow(r);
    }
    for (const [control, sw] of switches) {
      if (!control.isConnected) { sw.glass?.destroy(); switches.delete(control); }
    }
    for (const control of document.querySelectorAll<HTMLElement>('.gs-control')) {
      const sw = switches.get(control) ?? addSwitch(control);
      if (sw) syncSwitch(sw);
    }
  });
}

document.addEventListener('click', event => {
  const option = (event.target as Element | null)?.closest?.(OPTION) as HTMLElement | null;
  const row = option?.closest(ROW) as HTMLElement | null;
  if (!option || !row || option.matches(':disabled')) return;
  const r = rows.get(row);
  if (!r) return;
  r.pending = option; r.pendingUntil = performance.now() + OPTIMISTIC_MS;
  moveRow(r);
  setTimeout(() => moveRow(r), OPTIMISTIC_MS + 50); // spring back if the state did not change
}, { capture: true });

const resize = new ResizeObserver(() => {
  for (const r of rows.values()) { layoutCopy(r); moveRow(r, true); }
});
new MutationObserver(records => {
  // Ignore our own writes inside the copies and knobs.
  if (records.every(m => (m.target as Element).closest?.('.seg-copy, .seg-shadow, .gs-control'))) return;
  scan();
  for (const row of rows.keys()) resize.observe(row);
}).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'disabled'] });
scan();
