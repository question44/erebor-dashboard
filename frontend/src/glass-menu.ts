// Liquid glass menus, after liquidglass.dev's <lg-menu> (vendor/liquid-glass.js, MIT), applied to
// Angular Material menus with class "glass-menu" (Material keeps focus, keyboard and submenus).
//
// - Body: in Chromium the panel really refracts the page behind it (backdrop-filter: url(),
//   lens 22 px, bezel 0.35, blur 6, rim 0.8); other engines (Zen/Firefox) get the library's
//   blur + saturate fallback. A light tint and shadow come from styles.scss.
// - Open/close: styles.scss replaces Material's animations with a spring that fades the panel
//   in while its glass unrolls downwards; closing plays it back (Material removes the panel
//   200 ms after it starts closing).
// - Hover: a lens springs between items and reveals an accent-coloured copy of the item under it.

import { createGlass, createSpring, type Glass, type Spring } from './vendor/liquid-glass.js';

const PANEL = '.mat-mdc-menu-panel.glass-menu';
const ITEM = '.mat-mdc-menu-item';
const INSET = 6; // the lens sits this far inside the panel's sides

interface Menu {
  panel: HTMLElement; content: HTMLElement; copy: HTMLElement;
  body: Glass | null; lens: Glass | null; ys: Spring;
  y: number; v: number; index: number; on: boolean; items: HTMLElement[]; spans: HTMLElement[];
}
const menus = new Map<HTMLElement, Menu>();

function build(panel: HTMLElement) {
  const content = panel.querySelector<HTMLElement>('.mat-mdc-menu-content');
  if (!content) return;
  const items = [...content.querySelectorAll<HTMLElement>(`:scope > ${ITEM}`)];
  const copy = document.createElement('div');
  copy.className = 'glass-menu__copy'; copy.setAttribute('aria-hidden', 'true');
  const spans = items.map(item => {
    const span = document.createElement('span');
    span.className = 'glass-menu__copy-item';
    const cs = getComputedStyle(item);
    span.style.paddingLeft = cs.paddingLeft; span.style.paddingRight = cs.paddingRight;
    span.style.font = cs.font; span.style.letterSpacing = cs.letterSpacing;
    const label = item.querySelector('.mat-mdc-menu-item-text') ?? item;
    span.append(label.cloneNode(true));
    copy.append(span);
    return span;
  });
  content.append(copy);
  const m = { panel, content, copy, body: null, lens: null, y: 0, v: 0, index: -1, on: false, items, spans } as unknown as Menu;
  m.ys = createSpring(0, (val, vel) => { m.y = val; m.v = vel; renderLens(m); }, { stiffness: 520, damping: 36 });
  layout(m);
  const w = panel.offsetWidth, h = panel.offsetHeight;
  const radius = parseFloat(getComputedStyle(panel).borderTopLeftRadius) || 18;
  m.body = createGlass(panel, { mode: 'backdrop', width: w, height: h, radius, refraction: 22, bezel: 0.35, curvature: 3,
    chroma: 0, blur: 6, specular: 0.8, specularWidth: 2.5, fallback: 'blur(14px) saturate(1.7)' });
  m.lens = createGlass(copy, { clip: true, hidden: true, refraction: 6, bezel: 0.5, curvature: 3, chroma: 0.15, specular: 0.4, specularWidth: 1.5 });
  items.forEach((item, i) => {
    item.addEventListener('pointerenter', () => highlight(m, i));
    item.addEventListener('focus', () => highlight(m, i));
  });
  panel.addEventListener('pointerleave', () => { if (!panel.contains(document.activeElement)) highlight(m, -1); });
  // Keyboard focus lands on an item before any pointer: show the lens there too.
  const focused = items.findIndex(item => item === document.activeElement || item.classList.contains('cdk-keyboard-focused'));
  if (focused >= 0) highlight(m, focused);
  menus.set(panel, m);
}

/** The copy's items mirror the real ones (same offsets), so the lens reveals the right label. */
function layout(m: Menu) {
  m.items.forEach((item, i) => {
    const s = m.spans[i].style;
    s.top = `${item.offsetTop}px`; s.height = `${item.offsetHeight}px`;
  });
}

function highlight(m: Menu, i: number) {
  const item = m.items[i];
  m.index = i;
  if (!item || item.matches(':disabled, [aria-disabled="true"]')) {
    m.lens?.update({ hidden: true });
    m.on = false;
    return;
  }
  m.ys.set(item.offsetTop, { immediate: !m.on });
  m.on = true;
}

function renderLens(m: Menu) {
  const item = m.items[m.index];
  if (!item || !m.lens) return;
  const stretch = Math.min(6, Math.abs(m.v) * 0.01);
  const w = m.content.offsetWidth - 2 * INSET + stretch, h = item.offsetHeight + stretch * 0.5;
  m.lens.update({ hidden: false, x: INSET - stretch / 2, y: m.y - stretch * 0.25, width: w, height: h, radius: 10 });
}

function scan() {
  for (const [panel, m] of menus) {
    if (!panel.isConnected) { m.body?.destroy(); m.lens?.destroy(); menus.delete(panel); }
  }
  for (const panel of document.querySelectorAll<HTMLElement>(PANEL)) {
    if (!menus.has(panel) && panel.offsetWidth) build(panel);
  }
}

new MutationObserver(() => requestAnimationFrame(scan)).observe(document.body, { childList: true, subtree: true });
