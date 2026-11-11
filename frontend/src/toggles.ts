// Sliding thumbs for the device cards' toggle rows (.device-actions): one pill per
// row follows the current option (.device-action.current) on a spring, stretches
// along its motion and swells while an option is pressed. It runs in every
// background mode: with Solid/Transparent the pill is painted by CSS; with Sequoia
// it is transparent and sequoia.ts draws liquid glass at its rectangle (its
// transform included, so the stretch shows in the glass too).
//
// A press moves the thumb at once (the state arrives a moment later from the
// API); if the state does not change, it springs back after a while.

const ROW = '.device-actions';
const OPTION = '.device-action';
const CURRENT = '.device-action.current';
const STIFFNESS = (2 * Math.PI / 0.45) ** 2; // ~0.45 s, like --spring
const DAMPING = 2 * 0.68 * Math.sqrt(STIFFNESS); // a small overshoot
const PRESS_SCALE = 1.08;
const STRETCH = 0.00012; // stretch per px/s of speed: peaks mid-slide
const MAX_STRETCH = 0.18;
const OPTIMISTIC_MS = 2500;

interface Axis { x: number; v: number; }
interface Thumb {
  el: HTMLElement;
  x: Axis; y: Axis; w: Axis; h: Axis; press: Axis;
  pressed: boolean;
  pending: HTMLElement | null; pendingUntil: number;
  placed: boolean;
}

const thumbs = new Map<HTMLElement, Thumb>();
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let frame = 0, last = 0, running = false;

function step(a: Axis, target: number, dt: number) {
  const f = -STIFFNESS * (a.x - target) - DAMPING * a.v;
  a.v += f * dt; a.x += a.v * dt;
}
const settled = (a: Axis, target: number) => Math.abs(a.x - target) < 0.3 && Math.abs(a.v) < 2;

function ensure(row: HTMLElement): Thumb {
  let t = thumbs.get(row);
  if (t && t.el.isConnected) return t;
  const el = document.createElement('span');
  el.className = 'toggle-thumb';
  el.setAttribute('aria-hidden', 'true');
  row.prepend(el);
  const axis = (): Axis => ({ x: 0, v: 0 });
  t = { el, x: axis(), y: axis(), w: axis(), h: axis(), press: { x: 1, v: 0 }, pressed: false, pending: null, pendingUntil: 0, placed: false };
  thumbs.set(row, t);
  return t;
}

function target(row: HTMLElement, t: Thumb): HTMLElement | null {
  const current = row.querySelector<HTMLElement>(CURRENT);
  if (t.pending && (performance.now() > t.pendingUntil || t.pending === current || !t.pending.isConnected)) t.pending = null;
  return t.pending ?? current;
}

function tick(ms: number) {
  const dt = Math.min(Math.max((ms - last) / 1000, 0), 1 / 30);
  last = ms;
  let moving = false;
  for (const [row, t] of thumbs) {
    if (!row.isConnected) { t.el.remove(); thumbs.delete(row); continue; }
    const option = target(row, t);
    if (!option) { if (t.el.classList.contains('shown')) t.el.classList.remove('shown'); t.placed = false; continue; }
    const rr = row.getBoundingClientRect(), or = option.getBoundingClientRect();
    if (!rr.width || !or.width) continue;
    const goal = { x: or.left - rr.left, y: or.top - rr.top, w: or.width, h: or.height };
    if (!t.placed || reduced.matches) {
      t.x = { x: goal.x, v: 0 }; t.y = { x: goal.y, v: 0 }; t.w = { x: goal.w, v: 0 }; t.h = { x: goal.h, v: 0 };
      t.placed = true;
    } else {
      step(t.x, goal.x, dt); step(t.y, goal.y, dt); step(t.w, goal.w, dt); step(t.h, goal.h, dt);
    }
    step(t.press, t.pressed && !reduced.matches ? PRESS_SCALE : 1, dt);
    const speed = Math.hypot(t.x.v, t.y.v);
    const stretch = reduced.matches ? 0 : Math.min(speed * STRETCH, MAX_STRETCH);
    const horizontal = Math.abs(t.x.v) >= Math.abs(t.y.v);
    const sx = t.press.x * (horizontal ? 1 + stretch : 1 - stretch * 0.5);
    const sy = t.press.x * (horizontal ? 1 - stretch * 0.5 : 1 + stretch);
    const s = t.el.style;
    s.width = `${t.w.x}px`; s.height = `${t.h.x}px`;
    s.transform = `translate(${t.x.x}px, ${t.y.x}px) scale(${sx}, ${sy})`;
    if (!t.el.classList.contains('shown')) t.el.classList.add('shown');
    if (!(settled(t.x, goal.x) && settled(t.y, goal.y) && settled(t.w, goal.w) && settled(t.h, goal.h)
      && settled(t.press, t.pressed ? PRESS_SCALE : 1)) || t.pending) moving = true;
  }
  if (moving) frame = requestAnimationFrame(tick); else running = false;
}

function kick() {
  if (running) return;
  running = true; last = performance.now();
  frame = requestAnimationFrame(tick);
}

function scan() {
  for (const row of document.querySelectorAll<HTMLElement>(ROW)) {
    if (row.querySelector(CURRENT) || thumbs.has(row)) ensure(row);
  }
  kick();
}

function rowOf(event: Event) {
  const option = (event.target as Element | null)?.closest?.(OPTION) as HTMLElement | null;
  const row = option?.closest(ROW) as HTMLElement | null;
  return option && row && !option.matches(':disabled') ? { option, row } : null;
}

document.addEventListener('pointerdown', event => {
  const hit = rowOf(event);
  if (!hit) return;
  const t = ensure(hit.row);
  t.pressed = true;
  kick();
}, { passive: true });
const release = () => { for (const t of thumbs.values()) t.pressed = false; kick(); };
document.addEventListener('pointerup', release, { passive: true });
document.addEventListener('pointercancel', release, { passive: true });
document.addEventListener('click', event => {
  const hit = rowOf(event);
  if (!hit || !thumbs.has(hit.row) && !hit.row.querySelector(CURRENT)) return;
  const t = ensure(hit.row);
  t.pending = hit.option; t.pendingUntil = performance.now() + OPTIMISTIC_MS;
  kick();
}, { capture: true });
addEventListener('resize', () => kick());
new MutationObserver(scan).observe(document.documentElement, {
  subtree: true, childList: true, attributes: true, attributeFilter: ['class'],
});
scan();
