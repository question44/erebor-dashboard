// The "Sequoia" page background: macOS Sequoia's wallpaper, rendered with
// WebGL2, and liquid glass under every card that bends that wallpaper. Firefox
// cannot use SVG filters in backdrop-filter, so the glass is drawn here, from
// the same pixels, instead of by CSS.
//
// - Wallpaper: a port of erebor's sequoia-re renderer (dist/sequoia.html; every
//   constant comes from Apple's binary, see that repository's NOTES.md). Apple's
//   scene data is not in this repository: the control API serves it to signed-in
//   users (/api/control/sequoia, settings.sequoia).
// - Frost: dual Kawase blur, radius 2, 2 passes, saturation 1.1, as Umbriel's
//   [appearance.blur] on erebor.
// - Glass: Umbriel's liquid-glass-lens.glsl (erebor's window glass) with the
//   values tuned on the desktop, drawn under each card's rectangle.
//
// The wallpaper renders at up to 30 fps (half the desktop speed); the glass is
// redrawn every animation frame so it follows scrolling and card animations.
// Hidden pages stop; "still" and prefers-reduced-motion render one frame.

interface SequoiaData {
  version: number;
  scene: {
    heroes: number[][]; placed: number[][]; background: number[][]; paths: number[][][];
    noiseLattice: { lo: number; hi: number; g: number[] };
  };
  meshInfo: Record<string, { vOffset: number; vCount: number; iOffset: number; iCount: number }>;
  mesh: string;
  gradients: string;
}

// Elements drawn as glass: everything that paints var(--paper) as a card.
export const GLASS_SELECTOR = [
  '.outlet-panel', '.action.mat-mdc-button', '.notice', '.service.mat-mdc-button', '.device-control',
  '.auth-card', '.user-card', '.fold-more',
  // Header controls sit straight on the wallpaper: glass pills keep them readable.
  'header .mat-mdc-chip', '.account-button.mat-mdc-button', '.edit-button.mat-mdc-icon-button:not(.active)',
].join(', ');
// Text drawn as glass (the title): the element's own text is made transparent by CSS.
export const GLASS_TEXT_SELECTOR = '.glass-text';
// Folded sections clip their cards (styles.scss .fold-clip.clipped, overflow-clip-margin 6px).
const CLIP_SELECTOR = '.fold-clip.clipped';
const CLIP_MARGIN = 6;

// ---- wallpaper constants (sequoia-re NOTES.md) ------------------------------
const LOOP_SECONDS = 300.0, LOOP_DISTANCE = 20.0, WRAP_OFFSET = 24.0, WRAP_BORDER = 4.0, CULL_AHEAD = 10.0;
const FOV = 39.7 * Math.PI / 180, PITCH = 177.12 * Math.PI / 180, CAMERA_Z = 2.0, PATH_PHASE = 0.1;
const YAW_GAIN = 1.2, NOISE_FREQ = 20.0 * 0.25, SEED_RANGE = 5.0;
const NEAR_Z = 0.01000025, Z_SCALE = 2.503395e-05;
const LIGHT_GRADIENT = 4, DARK_GRADIENT = 0, CROSSFADE_SECONDS = 3.0;
const SPEED = 0.5;       // erebor's desktop_speed
const MAX_FPS = 30;      // the motion is slow
const MAX_PIXELS = 4.2e6; // render-size cap for very large or dense screens

function b64(str: string): ArrayBuffer {
  const bin = atob(str); const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}
const fract = (x: number) => x - Math.floor(x);
function smootherstep01(x: number) { x = Math.min(Math.max(x, 0), 1); return x * x * x * (x * (x * 6 - 15) + 10); }

function yawEnvelope(p: number) {
  if (p < 0.158) return smootherstep01((p - 0.094) / 0.064);
  if (p > 0.83) return 1 - smootherstep01((p - 0.83) / 0.147);
  return 1;
}

function buildPath(pts: number[][]): (t: number) => number {
  const dense: number[][] = [];
  for (let s = 0; s + 3 < pts.length; s += 3) {
    const [p0, p1, p2, p3] = [pts[s], pts[s + 1], pts[s + 2], pts[s + 3]];
    for (let k = (s ? 1 : 0); k <= 64; k++) {
      const t = k / 64, m = 1 - t;
      const w0 = m * m * m, w1 = 3 * m * m * t, w2 = 3 * m * t * t, w3 = t * t * t;
      dense.push([w0 * p0[0] + w1 * p1[0] + w2 * p2[0] + w3 * p3[0], w0 * p0[1] + w1 * p1[1] + w2 * p2[1] + w3 * p3[1]]);
    }
  }
  const len = [0];
  for (let i = 1; i < dense.length; i++) {
    const dx = (dense[i][0] - dense[i - 1][0]) * 0.01, dy = dense[i][1] - dense[i - 1][1];
    len.push(len[i - 1] + Math.hypot(dx, dy));
  }
  const total = len[len.length - 1], N = 512, xs = new Float32Array(N);
  let j = 0;
  for (let i = 0; i < N; i++) {
    const target = total * i / N;
    while (j < len.length - 2 && len[j + 1] < target) j++;
    const f = (target - len[j]) / Math.max(len[j + 1] - len[j], 1e-9);
    xs[i] = dense[j][0] + f * (dense[j + 1][0] - dense[j][0]);
  }
  return t => { const f = t * N, i = Math.floor(f) % N, n = (i + 1) % N, r = f - Math.floor(f); return xs[i] + r * (xs[n] - xs[i]); };
}

function mul(a: Float32Array, b: Float32Array) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s;
  }
  return o;
}
const rotX = (t: number) => { const c = Math.cos(t), s = Math.sin(t); return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]); };
const rotY = (t: number) => { const c = Math.cos(t), s = Math.sin(t); return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]); };
const rotZ = (t: number) => { const c = Math.cos(t), s = Math.sin(t); return new Float32Array([c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]); };
const translate = (x: number, y: number, z: number) => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]);
function invertRigid(m: Float32Array) {
  const o = new Float32Array(16);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) o[c * 4 + r] = m[r * 4 + c];
  for (let r = 0; r < 3; r++) o[12 + r] = -(o[r] * m[12] + o[4 + r] * m[13] + o[8 + r] * m[14]);
  o[15] = 1; return o;
}
const ROT_SIGN = -1;
function cameraMatrices(eye: number[], yaw: number, pitch: number, aspect: number) {
  let adj = FOV * 0.5;
  if (aspect > 1) adj = Math.tan(Math.atan(adj) / aspect);
  const world = mul(mul(mul(translate(eye[0], eye[1], eye[2]), rotY(ROT_SIGN * yaw)),
    rotX(ROT_SIGN * (pitch - adj + Math.PI / 2))), rotZ(ROT_SIGN * Math.PI));
  const view = invertRigid(world);
  const t = Math.tan(FOV * 0.5);
  const [sx, sy] = aspect > 1 ? [1 / t, aspect / t] : [1 / (t * aspect), 1 / t];
  const proj = new Float32Array([sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, Z_SCALE, -1, 0, 0, NEAR_Z, 0]);
  return { view, proj };
}
const encodeScale = (s: number) => Math.trunc(Math.min(Math.max((s - 0.35) / 1.05, 0), 1) * 65535);
const decodeScale = (n: number) => 0.35 + n / 65535;
const UNIT = decodeScale(encodeScale(1.0));

// ---- shaders ------------------------------------------------------------------
const COMMON_FRAG = `
  uniform sampler2D uGradA, uGradB;
  uniform float uMix;
  vec3 lin2srgb(vec3 c) {
    c = max(c, 0.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
  }
  vec4 shade(float u) {
    vec3 c = texture(uGradA, vec2(clamp(u, 0.0, 1.0), 0.5)).rgb;
    c = mix(c, texture(uGradB, vec2(clamp(u, 0.0, 1.0), 0.5)).rgb, uMix);
    float n = fract(fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))) * 52.9829189);
    c += (n - 0.5) * (1.0 / 255.0);
    return vec4(lin2srgb(c), 1.0);
  }`;
const MESH_VS = `#version 300 es
  layout(location=0) in vec4 aPos;
  layout(location=1) in vec4 aInst;
  layout(location=2) in vec2 aScale;
  uniform mat4 uView, uProj;
  out float vU;
  void main() {
    float a = -aPos.x * aScale.x, b = aPos.y * aScale.x;
    float s = sin(aInst.z), c = cos(aInst.z);
    vec2 r = vec2(c, s) * a + vec2(-s, c) * b;
    vec3 world = vec3(r.x - aInst.x, aPos.z * aScale.y, r.y + aInst.y);
    gl_Position = uProj * uView * vec4(world, 1.0);
    vU = aPos.w;
  }`;
const MESH_FS = `#version 300 es
  precision highp float;
  in float vU; out vec4 o;
  ${COMMON_FRAG}
  void main() { o = shade(vU); }`;
const FULL_VS = `#version 300 es
  out vec2 vUv;
  void main() {
    vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1)) * 4.0 - 1.0;
    vUv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
  }`;
const BG_FS = `#version 300 es
  precision highp float;
  out vec4 o;
  ${COMMON_FRAG}
  void main() { o = shade(1.0); }`;
const COPY_FS = `#version 300 es
  precision highp float;
  uniform sampler2D uTex; in vec2 vUv; out vec4 o;
  void main() { o = texture(uTex, vUv); }`;
// Dual Kawase (Umbriel's blur): uHalf = offset * half a source texel.
const DOWN_FS = `#version 300 es
  precision highp float;
  uniform sampler2D uTex; uniform vec2 uHalf; in vec2 vUv; out vec4 o;
  void main() {
    vec4 s = texture(uTex, vUv) * 4.0;
    s += texture(uTex, vUv - uHalf);
    s += texture(uTex, vUv + uHalf);
    s += texture(uTex, vUv + vec2(uHalf.x, -uHalf.y));
    s += texture(uTex, vUv - vec2(uHalf.x, -uHalf.y));
    o = s / 8.0;
  }`;
const UP_FS = `#version 300 es
  precision highp float;
  uniform sampler2D uTex; uniform vec2 uHalf; uniform float uSaturation; in vec2 vUv; out vec4 o;
  void main() {
    vec4 s = texture(uTex, vUv + vec2(-uHalf.x * 2.0, 0.0));
    s += texture(uTex, vUv + vec2(-uHalf.x, uHalf.y)) * 2.0;
    s += texture(uTex, vUv + vec2(0.0, uHalf.y * 2.0));
    s += texture(uTex, vUv + vec2(uHalf.x, uHalf.y)) * 2.0;
    s += texture(uTex, vUv + vec2(uHalf.x * 2.0, 0.0));
    s += texture(uTex, vUv + vec2(uHalf.x, -uHalf.y)) * 2.0;
    s += texture(uTex, vUv + vec2(0.0, -uHalf.y * 2.0));
    s += texture(uTex, vUv + vec2(-uHalf.x, -uHalf.y)) * 2.0;
    vec3 c = s.rgb / 12.0;
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    o = vec4(max(mix(vec3(l), c, uSaturation), 0.0), 1.0);
  }`;
// One card: a quad over its rectangle (canvas px, y down).
const GLASS_VS = `#version 300 es
  uniform vec4 uRect; uniform vec2 uScreen;
  void main() {
    int i = gl_VertexID; // two triangles: (0,0) (1,0) (1,1) / (0,0) (1,1) (0,1)
    vec2 c = vec2(i == 1 || i == 2 || i == 4 ? 1.0 : 0.0, i == 2 || i == 4 || i == 5 ? 1.0 : 0.0);
    vec2 px = uRect.xy + c * uRect.zw;
    gl_Position = vec4(px.x / uScreen.x * 2.0 - 1.0, 1.0 - px.y / uScreen.y * 2.0, 0.0, 1.0);
  }`;
// erebor's home/umbriel/effects/liquid-glass-lens.glsl, values as tuned on the desktop.
// Lengths are logical px, scaled by uDpr; uv offsets became px offsets.
const GLASS_FS = `#version 300 es
  precision highp float;
  uniform sampler2D uBlur; uniform vec2 uScreen; uniform vec4 uRect; uniform vec4 uRadius; uniform float uDpr;
  // Per element: tint over the glass (rgb, amount), white lift, specular gain,
  // and hover (0..1) with the pointer position for a highlight that follows it.
  uniform vec4 uTint; uniform float uLift, uSpec, uHover; uniform vec2 uPointer;
  out vec4 o;
  const float REFRACTION = 47.0;
  const float BEZEL = 61.0;
  const float CURVATURE = 4.8;
  const float IOR = 2.12;
  const float CHROMA = 0.8;
  const float SPECULAR = 0.06;
  const float SPECULAR_W = 6.6;
  const float LIGHT = 41.0;
  const float RIM_OPACITY = 0.48;
  const float RIM_SATURATION = 3.5;
  const float RIM_WHITE = 0.17;
  const float RIM_W = 4.6;
  const float RIM_LIGHT = -53.0;
  float sdRoundBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }
  float profile(float s) {
    if (s >= 1.0) return 0.0;
    float a = pow(1.0 - s, CURVATURE);
    float slope = pow(1.0 - s, CURVATURE - 1.0) * pow(max(1.0 - a, 1e-6), 1.0 / CURVATURE - 1.0);
    float th = atan(slope);
    float bend = tan(th - asin(sin(th) / IOR));
    return bend / tan(1.5707963 - asin(1.0 / IOR));
  }
  vec3 samplePx(vec2 px) {
    vec2 uv = vec2(px.x, uScreen.y - px.y) / uScreen;
    return texture(uBlur, clamp(uv, vec2(0.0), vec2(1.0))).rgb;
  }
  void main() {
    vec2 frag = vec2(gl_FragCoord.x, uScreen.y - gl_FragCoord.y);
    vec2 halfSize = uRect.zw * 0.5;
    vec2 p = frag - (uRect.xy + halfSize);
    vec4 c = uRadius;
    float r = p.y < 0.0 ? (p.x < 0.0 ? c.x : c.y) : (p.x < 0.0 ? c.w : c.z);
    float hmin = min(halfSize.x, halfSize.y);
    r = min(r, hmin);
    float sd = sdRoundBox(p, halfSize, r);
    float cover = clamp(0.5 - sd, 0.0, 1.0);
    if (cover <= 0.0) discard;
    float s = max(-sd, 0.0);
    float bezelPx = BEZEL * uDpr, refraction = REFRACTION * uDpr;
    float rn = min(max(r, min(bezelPx, hmin * 0.5)), hmin);
    float h = 3.0;
    vec2 g = vec2(
      sdRoundBox(p + vec2(h, 0.0), halfSize, rn) - sdRoundBox(p - vec2(h, 0.0), halfSize, rn),
      sdRoundBox(p + vec2(0.0, h), halfSize, rn) - sdRoundBox(p - vec2(0.0, h), halfSize, rn));
    vec2 n = length(g) > 0.0 ? normalize(g) : vec2(0.0);
    float bezel = min(bezelPx, hmin * 0.5);
    float m = profile(s / bezel);
    float px = min(m * refraction, hmin / (1.0 + CHROMA));
    vec2 off = -n * px;
    vec3 col = m > 0.0
      ? vec3(samplePx(frag + off).r, samplePx(frag + off * (1.0 + CHROMA * 0.5)).g, samplePx(frag + off * (1.0 + CHROMA)).b)
      : samplePx(frag);
    float la = radians(LIGHT);
    float facing = dot(n, vec2(cos(la), sin(la)));
    float shade = 0.12 + 0.88 * (facing > 0.0 ? facing : -0.4 * facing);
    float crisp = max(0.0, 1.0 - s / (SPECULAR_W * uDpr));
    float glow = max(0.0, 1.0 - s / (SPECULAR_W * 5.0 * uDpr));
    col = mix(col, vec3(1.0), uLift);
    col = mix(col, uTint.rgb, uTint.a);
    float spec = SPECULAR * uSpec * (1.0 + uHover);
    col += min(1.0, spec * (crisp * crisp + 0.3 * glow * glow) * shade);
    float ra = radians(RIM_LIGHT);
    float edge = max(0.0, 1.0 - s / (RIM_W * uDpr));
    float rim = RIM_OPACITY * edge * edge * abs(dot(n, vec2(cos(ra), sin(ra)))) * (1.0 + 0.8 * uHover);
    float lum = dot(col, vec3(0.2126, 0.7152, 0.0722));
    vec3 vivid = max(mix(vec3(lum), col, RIM_SATURATION), 0.0);
    col = mix(col, vivid, min(rim, 1.0));
    col = mix(col, vec3(1.0), min(rim, 1.0) * RIM_WHITE);
    // Hover: a soft light under the pointer.
    vec2 dp = (frag - uPointer) / (150.0 * uDpr);
    col += uHover * 0.10 * exp(-dot(dp, dp));
    o = vec4(col * cover, cover);
  }`;

// Glass text: the lens optics on a glyph mask. uMask holds coverage (R) and the
// distance inside (G) and outside (B) the glyphs, in px / TEXT_MAXD.
const TEXT_MAXD = 16; // logical px
const TEXT_VS = `#version 300 es
  uniform vec4 uRect; uniform vec2 uScreen;
  out vec2 vUv;
  void main() {
    int i = gl_VertexID;
    vec2 c = vec2(i == 1 || i == 2 || i == 4 ? 1.0 : 0.0, i == 2 || i == 4 || i == 5 ? 1.0 : 0.0);
    vUv = c;
    vec2 px = uRect.xy + c * uRect.zw;
    gl_Position = vec4(px.x / uScreen.x * 2.0 - 1.0, 1.0 - px.y / uScreen.y * 2.0, 0.0, 1.0);
  }`;
const TEXT_FS = `#version 300 es
  precision highp float;
  uniform sampler2D uBlur, uMask; uniform vec2 uScreen; uniform vec4 uRect; uniform float uDpr, uMaxD, uShadow, uLift;
  in vec2 vUv; out vec4 o;
  // Each stroke is a rounded glass rod: the height comes from the distance to the
  // edge relative to the stroke's own half-width, so the whole stroke refracts.
  const float REFRACTION = 16.0; // logical px, capped by the stroke's half-width
  const float CURVATURE = 2.6;   // rod profile: 2 is circular
  const float IOR = 1.9;
  const float CHROMA = 0.6;
  const float SPECULAR = 0.55;
  const float LIGHT = -120.0;   // from the upper left, as text is usually lit
  const float RIM_OPACITY = 0.5;
  const float RIM_SATURATION = 3.0;
  const float RIM_W = 1.6;
  float profile(float s) {
    if (s >= 1.0) return 0.0;
    float a = pow(1.0 - s, CURVATURE);
    float slope = pow(1.0 - s, CURVATURE - 1.0) * pow(max(1.0 - a, 1e-6), 1.0 / CURVATURE - 1.0);
    float th = atan(slope);
    return tan(th - asin(sin(th) / IOR)) / tan(1.5707963 - asin(1.0 / IOR));
  }
  vec3 samplePx(vec2 px) {
    vec2 uv = vec2(px.x, uScreen.y - px.y) / uScreen;
    return texture(uBlur, clamp(uv, vec2(0.0), vec2(1.0))).rgb;
  }
  float sdAt(vec2 uv) { vec3 m = texture(uMask, uv).rgb; return (m.b - m.g) * uMaxD; }
  float insideAt(vec2 uv) { return texture(uMask, uv).g * uMaxD; }
  void main() {
    vec2 texel = 1.0 / vec2(textureSize(uMask, 0));
    vec3 m = texture(uMask, vUv).rgb;
    float cover = m.r, inside = m.g * uMaxD;
    // A faint contact shadow just below the letters.
    float below = texture(uMask, vUv - vec2(0.0, 1.5 * uDpr) * texel).b * uMaxD;
    float shadow = uShadow * pow(max(0.0, 1.0 - below / (5.0 * uDpr)), 2.0);
    if (cover <= 0.002 && shadow <= 0.002) discard;
    vec2 frag = vec2(gl_FragCoord.x, uScreen.y - gl_FragCoord.y);
    vec2 g = vec2(sdAt(vUv + vec2(texel.x, 0.0)) - sdAt(vUv - vec2(texel.x, 0.0)),
                  sdAt(vUv + vec2(0.0, texel.y)) - sdAt(vUv - vec2(0.0, texel.y)));
    // The mask's rows run downwards, like frag: g is already in frag coordinates.
    vec2 n = length(g) > 1e-4 ? normalize(g) : vec2(0.0); // outward
    vec2 nUv = n * texel;
    float s = max(inside + cover - 0.5, 0.0);
    // The stroke's half-width: the inside distance on the ridge, found by stepping inward.
    float half_ = max(s, 0.5 * uDpr);
    for (int i = 1; i <= 8; i++) half_ = max(half_, insideAt(vUv - nUv * (half_ * 0.5 + float(i) * 0.6 * uDpr)));
    float u = clamp(s / half_, 0.0, 1.0);          // 0 at the edge, 1 on the ridge
    float mprof = profile(u);
    vec2 off = -n * mprof * min(REFRACTION * uDpr, half_ * 1.4);
    vec3 col = vec3(samplePx(frag + off).r, samplePx(frag + off * (1.0 + CHROMA * 0.5)).g, samplePx(frag + off * (1.0 + CHROMA)).b);
    col = mix(col, vec3(1.0), uLift);
    // Light: a bright edge on the side facing the light, a darker one opposite.
    float la = radians(LIGHT);
    float facing = dot(n, vec2(cos(la), sin(la)));
    float edge = pow(1.0 - u, 3.0);
    col += SPECULAR * edge * max(facing, 0.0);
    col *= 1.0 - 0.22 * edge * max(-facing, 0.0);
    // A thin coloured rim (kube.io), and a soft sheen along the ridge.
    float rimEdge = max(0.0, 1.0 - s / (RIM_W * uDpr));
    float rim = RIM_OPACITY * rimEdge * rimEdge;
    float lum = dot(col, vec3(0.2126, 0.7152, 0.0722));
    col = mix(col, max(mix(vec3(lum), col, RIM_SATURATION), 0.0), rim);
    col += 0.06 * smoothstep(0.6, 1.0, u);
    float a = cover + shadow * (1.0 - cover);
    o = vec4(col * cover, a);
  }`;

// Squared Euclidean distance transform (Felzenszwalb & Huttenlocher), in place on f (w x h).
function edt(f: Float32Array, w: number, h: number) {
  const n = Math.max(w, h), d = new Float32Array(n), v = new Int32Array(n), z = new Float32Array(n + 1), g = new Float32Array(n);
  const pass = (len: number, get: (i: number) => number, set: (i: number, x: number) => void) => {
    for (let i = 0; i < len; i++) g[i] = get(i);
    let k = 0; v[0] = 0; z[0] = -Infinity; z[1] = Infinity;
    for (let q = 1; q < len; q++) {
      let sv = ((g[q] + q * q) - (g[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
      while (sv <= z[k]) { k--; sv = ((g[q] + q * q) - (g[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]); }
      k++; v[k] = q; z[k] = sv; z[k + 1] = Infinity;
    }
    k = 0;
    for (let q = 0; q < len; q++) { while (z[k + 1] < q) k++; d[q] = (q - v[k]) * (q - v[k]) + g[v[k]]; }
    for (let i = 0; i < len; i++) set(i, d[i]);
  };
  for (let x = 0; x < w; x++) pass(h, y => f[y * w + x], (y, val) => { f[y * w + x] = val; });
  for (let y = 0; y < h; y++) pass(w, x => f[y * w + x], (x, val) => { f[y * w + x] = val; });
}

interface TextMask { key: string; tex: WebGLTexture; w: number; h: number; pad: number; }

interface Target { tex: WebGLTexture; fb: WebGLFramebuffer; w: number; h: number; }

export class SequoiaRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly instances: { mesh: string; x: number; z: number; rot: number; radial: number; vertical: number }[] = [];
  private readonly paths: ((t: number) => number)[];
  private readonly lattice: SequoiaData['scene']['noiseLattice'];
  private readonly meshes: Record<string, { vao: WebGLVertexArrayObject; instBuf: WebGLBuffer; count: number; data: Float32Array; n: number }> = {};
  private readonly gradTex: WebGLTexture[] = [];
  private readonly progs: Record<string, WebGLProgram> = {};
  private readonly emptyVao: WebGLVertexArrayObject;
  private msaa: { fb: WebGLFramebuffer; color: WebGLRenderbuffer; depth: WebGLRenderbuffer; samples: number } | null = null;
  private scene: Target | null = null;
  private chain: Target[] = [];
  private width = 0; private height = 0; private dpr = 1;
  private progress = Math.random();
  private pathIndex: number;
  private seed = -SEED_RANGE + Math.random() * 2 * SEED_RANGE;
  private gradFrom: number; private gradTo: number; private fadeStart = 0;
  private frame = 0; private last: number | null = null; private lastScene = -1e9;
  private sceneDirty = true;
  private stopped = false;
  private radii = new WeakMap<Element, number[]>();
  private masks = new WeakMap<Element, TextMask>();
  private hovers = new WeakMap<Element, number>();
  private hovering = false;
  private pointer = { x: -1e4, y: -1e4 };
  private lastComposite = 0;
  private readonly onPointer = (e: PointerEvent) => { this.pointer = { x: e.clientX, y: e.clientY }; this.schedule(); };
  private readonly dark = matchMedia('(prefers-color-scheme: dark)');
  private readonly reduced = matchMedia('(prefers-reduced-motion: reduce)');
  private readonly onScheme = () => this.setGradient(this.dark.matches ? DARK_GRADIENT : LIGHT_GRADIENT);
  private readonly onResize = () => { this.radii = new WeakMap(); this.sceneDirty = true; this.schedule(); };
  private readonly onVisibility = () => { if (!document.hidden) { this.last = null; this.schedule(); } };
  private readonly onChange = () => this.schedule();
  // Cards move without a resize or scroll: section folds, drags, hover and spring animations.
  private readonly observer = new MutationObserver(this.onChange);
  // Its CSS size changes without a window resize on phones (100lvh as the browser bars move).
  private readonly sizeObserver = new ResizeObserver(() => this.onResize());

  constructor(private readonly canvas: HTMLCanvasElement, data: SequoiaData, private readonly still: () => boolean) {
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, depth: false, premultipliedAlpha: true });
    if (!gl) throw new Error('WebGL2 unavailable');
    this.gl = gl;
    const scene = data.scene;
    this.lattice = scene.noiseLattice;
    this.paths = scene.paths.map(buildPath);
    this.pathIndex = Math.floor(Math.random() * this.paths.length);
    scene.heroes.forEach((p, i) => this.instances.push({ mesh: i ? 'hero2' : 'hero1', x: p[0], z: p[1], rot: 0, radial: UNIT, vertical: UNIT }));
    scene.placed.forEach(p => this.instances.push({ mesh: 'common', x: p[0], z: p[1], rot: p[2],
      radial: decodeScale(encodeScale(p[3])), vertical: decodeScale(encodeScale(p[4])) }));
    let rng = 0x9e3779b9;
    const rand01 = () => { rng ^= rng << 13; rng >>>= 0; rng ^= rng >>> 17; rng ^= rng << 5; rng >>>= 0; return rng / 4294967296; };
    scene.background.forEach(p => this.instances.push({ mesh: 'common', x: p[0], z: p[1], rot: rand01() * Math.PI * 2, radial: UNIT, vertical: UNIT }));

    this.progs['mesh'] = this.program(MESH_VS, MESH_FS);
    this.progs['bg'] = this.program(FULL_VS, BG_FS);
    this.progs['copy'] = this.program(FULL_VS, COPY_FS);
    this.progs['down'] = this.program(FULL_VS, DOWN_FS);
    this.progs['up'] = this.program(FULL_VS, UP_FS);
    this.progs['glass'] = this.program(GLASS_VS, GLASS_FS);
    this.progs['text'] = this.program(TEXT_VS, TEXT_FS);
    this.emptyVao = gl.createVertexArray()!;

    const meshBuf = b64(data.mesh);
    for (const [name, info] of Object.entries(data.meshInfo)) {
      const vao = gl.createVertexArray()!; gl.bindVertexArray(vao);
      const vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vb);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(meshBuf, info.vOffset, info.vCount * 4), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 16, 0);
      const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(meshBuf, info.iOffset, info.iCount), gl.STATIC_DRAW);
      const instBuf = gl.createBuffer()!; gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
      gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 24, 0); gl.vertexAttribDivisor(1, 1);
      gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 2, gl.FLOAT, false, 24, 16); gl.vertexAttribDivisor(2, 1);
      this.meshes[name] = { vao, instBuf, count: info.iCount, data: new Float32Array(this.instances.length * 6), n: 0 };
    }
    gl.bindVertexArray(null);
    const linear = gl.getExtension('OES_texture_float_linear') ? gl.LINEAR : gl.NEAREST;
    const grads = new Float32Array(b64(data.gradients));
    for (let i = 0; i < 8; i++) {
      const t = gl.createTexture()!; gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, 512, 1, 0, gl.RGB, gl.FLOAT, grads.subarray(i * 1536, (i + 1) * 1536));
      this.params(linear);
      this.gradTex.push(t);
    }
    this.gradFrom = this.gradTo = this.dark.matches ? DARK_GRADIENT : LIGHT_GRADIENT;
    this.dark.addEventListener('change', this.onScheme);
    addEventListener('resize', this.onResize);
    addEventListener('scroll', this.onChange, { passive: true, capture: true });
    document.addEventListener('visibilitychange', this.onVisibility);
    for (const type of ['transitionrun', 'animationstart']) document.addEventListener(type, this.onChange, { passive: true });
    document.addEventListener('pointermove', this.onPointer, { passive: true });
    document.addEventListener('pointerleave', this.onPointer, { passive: true });
    this.observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'style'] });
    this.sizeObserver.observe(canvas);
    visualViewport?.addEventListener('resize', this.onResize);
    this.schedule();
  }

  stop() {
    this.stopped = true;
    cancelAnimationFrame(this.frame);
    this.dark.removeEventListener('change', this.onScheme);
    removeEventListener('resize', this.onResize);
    removeEventListener('scroll', this.onChange, { capture: true });
    document.removeEventListener('visibilitychange', this.onVisibility);
    for (const type of ['transitionrun', 'animationstart']) document.removeEventListener(type, this.onChange);
    document.removeEventListener('pointermove', this.onPointer);
    document.removeEventListener('pointerleave', this.onPointer);
    this.observer.disconnect();
    this.sizeObserver.disconnect();
    visualViewport?.removeEventListener('resize', this.onResize);
    this.gl.getExtension('WEBGL_lose_context')?.loseContext();
  }

  /** Redraw soon: layout changed (cards moved), or the still frame needs refreshing. */
  schedule() {
    if (this.stopped || document.hidden) return;
    this.idleFrames = 0;
    if (this.running) return;
    this.running = true;
    this.frame = requestAnimationFrame(ms => this.tick(ms));
  }
  private running = false;
  private idleFrames = 0;

  private animated() { return !this.still() && !this.reduced.matches; }

  private setGradient(g: number) {
    if (g === this.gradTo) return;
    this.gradFrom = this.gradTo; this.gradTo = g; this.fadeStart = performance.now() / 1000;
    this.sceneDirty = true; this.schedule();
  }

  private tick(ms: number) {
    if (this.stopped || document.hidden) { this.running = false; return; }
    const fading = this.gradFrom !== this.gradTo;
    const animated = this.animated();
    // Keep going while the wallpaper moves or fades; otherwise redraw only on
    // request (scroll, resize, layout) and follow card movement for about a second.
    this.resize();
    if ((animated || fading) && ms - this.lastScene >= 1000 / MAX_FPS - 1) {
      const dt = this.last === null ? 0 : (ms - this.last) / 1000;
      this.last = ms;
      if (animated) this.advance(dt);
      this.sceneDirty = true;
    }
    if (this.sceneDirty) { this.renderScene(ms / 1000); this.blur(); this.lastScene = ms; this.sceneDirty = false; }
    this.composite();
    if (this.hovering) this.idleFrames = 0;
    if (!animated && !fading && ++this.idleFrames > 60) { this.running = false; this.last = null; return; }
    this.frame = requestAnimationFrame(t => this.tick(t));
  }

  private advance(dt: number) {
    this.progress += dt * SPEED / LOOP_SECONDS;
    if (this.progress > 1) {
      this.progress -= 1;
      this.pathIndex = Math.floor(Math.random() * this.paths.length);
      this.seed = -SEED_RANGE + Math.random() * 2 * SEED_RANGE;
    }
  }

  private resize() {
    const dpr = window.devicePixelRatio || 1;
    let w = Math.round(this.canvas.clientWidth * dpr), h = Math.round(this.canvas.clientHeight * dpr);
    let scale = dpr;
    if (w * h > MAX_PIXELS) { const k = Math.sqrt(MAX_PIXELS / (w * h)); w = Math.round(w * k); h = Math.round(h * k); scale = dpr * k; }
    if (w < 1 || h < 1) return;
    if (w === this.width && h === this.height) { this.dpr = scale; return; }
    this.width = w; this.height = h; this.dpr = scale;
    this.canvas.width = w; this.canvas.height = h;
    this.allocate();
    this.sceneDirty = true;
  }

  private allocate() {
    const gl = this.gl;
    const free = (t: Target | null) => { if (t) { gl.deleteTexture(t.tex); gl.deleteFramebuffer(t.fb); } };
    free(this.scene); this.chain.forEach(free);
    if (this.msaa) { gl.deleteFramebuffer(this.msaa.fb); gl.deleteRenderbuffer(this.msaa.color); gl.deleteRenderbuffer(this.msaa.depth); }
    const samples = Math.min(4, gl.getParameter(gl.MAX_SAMPLES) as number);
    const fb = gl.createFramebuffer()!; gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    const color = gl.createRenderbuffer()!; gl.bindRenderbuffer(gl.RENDERBUFFER, color);
    gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.RGBA8, this.width, this.height);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, color);
    const depth = gl.createRenderbuffer()!; gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.DEPTH_COMPONENT32F, this.width, this.height);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    this.msaa = { fb, color, depth, samples };
    this.scene = this.target(this.width, this.height);
    // Dual Kawase: 1/2, 1/4 down, then back up to 1/2 (sampled with linear filtering).
    this.chain = [this.target(this.width >> 1, this.height >> 1), this.target(this.width >> 2, this.height >> 2),
      this.target(this.width >> 1, this.height >> 1)];
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  private target(w: number, h: number): Target {
    const gl = this.gl;
    w = Math.max(w, 1); h = Math.max(h, 1);
    const tex = gl.createTexture()!; gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    this.params(gl.LINEAR);
    const fb = gl.createFramebuffer()!; gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    return { tex, fb, w, h };
  }

  private params(filter: number) {
    const gl = this.gl;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  private program(vs: string, fs: string) {
    const gl = this.gl, p = gl.createProgram()!;
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]] as const) {
      const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader');
      gl.attachShader(p, s);
    }
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'link');
    return p;
  }

  private u(prog: string, name: string) { return this.gl.getUniformLocation(this.progs[prog], name); }

  private noise2(x: number, y: number) {
    const NL = this.lattice, N = NL.hi - NL.lo + 1;
    const grad = (i: number, j: number) => {
      i = Math.min(Math.max(i - NL.lo, 0), N - 1); j = Math.min(Math.max(j - NL.lo, 0), N - 1);
      const k = (i * N + j) * 2; return [NL.g[k], NL.g[k + 1]];
    };
    const i = Math.floor(x), j = Math.floor(y), rx = x - i, ry = y - j;
    const a = grad(i, j), b = grad(i + 1, j), c = grad(i, j + 1), d = grad(i + 1, j + 1);
    const sx = smootherstep01(rx), sy = smootherstep01(ry);
    const u0 = rx * a[0] + ry * a[1], v0 = (rx - 1) * b[0] + ry * b[1];
    const u1 = rx * c[0] + (ry - 1) * c[1], v1 = (rx - 1) * d[0] + (ry - 1) * d[1];
    const A = u0 + sx * (v0 - u0), B = u1 + sx * (v1 - u1);
    return A + sy * (B - A);
  }

  private renderScene(nowSec: number) {
    const gl = this.gl, msaa = this.msaa!, scene = this.scene!;
    const p = this.progress;
    const eye = [-this.paths[this.pathIndex](fract(p + PATH_PHASE)), 0, CAMERA_Z];
    const yaw = this.noise2(this.seed + p * NOISE_FREQ, p * NOISE_FREQ - this.seed) * yawEnvelope(p) * YAW_GAIN;
    const { view, proj } = cameraMatrices(eye, yaw, PITCH, this.width / this.height);
    for (const m of Object.values(this.meshes)) m.n = 0;
    for (const inst of this.instances) {
      const z = ((inst.z - LOOP_DISTANCE * p + WRAP_OFFSET) % LOOP_DISTANCE) - WRAP_BORDER;
      if (z > CULL_AHEAD) continue;
      const m = this.meshes[inst.mesh], o = m.n * 6;
      m.data[o] = inst.x; m.data[o + 1] = z; m.data[o + 2] = inst.rot; m.data[o + 3] = 0;
      m.data[o + 4] = inst.radial; m.data[o + 5] = inst.vertical; m.n++;
    }
    const wt = (2 * Math.PI / CROSSFADE_SECONDS) * Math.max(nowSec - this.fadeStart, 0);
    const fade = this.gradFrom === this.gradTo ? 1 : (wt > 12 ? 1 : 1 - (1 + wt) * Math.exp(-wt));
    if (fade >= 1) this.gradFrom = this.gradTo;
    const bindGradients = (prog: string) => {
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.gradTex[this.gradFrom]);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.gradTex[this.gradTo]);
      gl.uniform1i(this.u(prog, 'uGradA'), 0); gl.uniform1i(this.u(prog, 'uGradB'), 1);
      gl.uniform1f(this.u(prog, 'uMix'), this.gradFrom === this.gradTo ? 0 : fade);
    };
    gl.bindFramebuffer(gl.FRAMEBUFFER, msaa.fb);
    gl.viewport(0, 0, this.width, this.height);
    gl.disable(gl.BLEND);
    gl.clearDepth(0); gl.clear(gl.DEPTH_BUFFER_BIT);
    gl.disable(gl.CULL_FACE); gl.disable(gl.DEPTH_TEST);
    gl.useProgram(this.progs['bg']); bindGradients('bg');
    gl.bindVertexArray(this.emptyVao); gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.GREATER);
    gl.useProgram(this.progs['mesh']); bindGradients('mesh');
    gl.uniformMatrix4fv(this.u('mesh', 'uView'), false, view);
    gl.uniformMatrix4fv(this.u('mesh', 'uProj'), false, proj);
    for (const m of Object.values(this.meshes)) {
      if (!m.n) continue;
      gl.bindVertexArray(m.vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, m.instBuf);
      gl.bufferData(gl.ARRAY_BUFFER, m.data.subarray(0, m.n * 6), gl.DYNAMIC_DRAW);
      gl.drawElementsInstanced(gl.TRIANGLES, m.count, gl.UNSIGNED_INT, 0, m.n);
    }
    gl.bindVertexArray(null);
    gl.disable(gl.DEPTH_TEST);
    // Resolve the 4x MSAA scene into a texture.
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, msaa.fb);
    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, scene.fb);
    gl.blitFramebuffer(0, 0, this.width, this.height, 0, 0, this.width, this.height, gl.COLOR_BUFFER_BIT, gl.NEAREST);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, null);
  }

  private pass(prog: string, src: Target, dst: Target, saturation = 1) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb);
    gl.viewport(0, 0, dst.w, dst.h);
    gl.useProgram(this.progs[prog]);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, src.tex);
    gl.uniform1i(this.u(prog, 'uTex'), 0);
    // Umbriel's radius 2 at the desktop's pixel density.
    const offset = 2 * Math.max(this.dpr, 1);
    gl.uniform2f(this.u(prog, 'uHalf'), offset * 0.5 / src.w, offset * 0.5 / src.h);
    if (prog === 'up') gl.uniform1f(this.u(prog, 'uSaturation'), saturation);
    gl.bindVertexArray(this.emptyVao); gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  private blur() {
    const [half, quarter, up] = this.chain;
    this.gl.disable(this.gl.BLEND);
    this.pass('down', this.scene!, half);
    this.pass('down', half, quarter);
    this.pass('up', quarter, up, 1.1);
  }

  private composite() {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.width, this.height);
    gl.disable(gl.BLEND);
    gl.useProgram(this.progs['copy']);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.scene!.tex);
    gl.uniform1i(this.u('copy', 'uTex'), 0);
    gl.bindVertexArray(this.emptyVao); gl.drawArrays(gl.TRIANGLES, 0, 3);

    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.progs['glass']);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.chain[2].tex);
    gl.uniform1i(this.u('glass', 'uBlur'), 0);
    gl.uniform2f(this.u('glass', 'uScreen'), this.width, this.height);
    gl.uniform1f(this.u('glass', 'uDpr'), this.dpr);
    const k = this.width / Math.max(this.canvas.clientWidth, 1);
    gl.uniform2f(this.u('glass', 'uPointer'), this.pointer.x * k, this.pointer.y * k);
    const now = performance.now(), dt = Math.min((now - this.lastComposite) / 1000, 0.1);
    this.lastComposite = now;
    this.hovering = false;
    const theme = getComputedStyle(document.documentElement);
    const fill = (parseFloat(theme.getPropertyValue('--glass-fill')) || 42) / 100;
    const paper = parseColor(theme.getPropertyValue('--paper'), [0.95, 0.93, 0.97]);
    // Cards: tinted by the page's paper colour, as their CSS background was.
    for (const el of document.querySelectorAll(GLASS_SELECTOR)) {
      this.drawGlass(el, k, dt, [...paper, fill], 0, 1, true, false);
    }
    gl.disable(gl.SCISSOR_TEST);
    this.drawText(k);
    gl.disable(gl.BLEND);
    gl.bindVertexArray(null);
  }

  /** One glass shape at el's rectangle (transforms included); pills use half the short side as radius. */
  private drawGlass(el: Element, k: number, dt: number, tint: number[], lift: number, spec: number, hover: boolean, pill: boolean) {
    const gl = this.gl;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) return;
    if (!this.clip(el, r, k)) return;
    let radii = pill ? undefined : this.radii.get(el);
    if (pill) {
      const rr = Math.min(r.width, r.height) / 2; radii = [rr, rr, rr, rr];
    } else if (!radii) {
      const cs = getComputedStyle(el);
      radii = [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius, cs.borderBottomLeftRadius]
        .map(v => parseFloat(v) || 0);
      this.radii.set(el, radii);
    }
    let h = 0;
    if (hover) {
      const want = el.matches(':hover') && !el.matches(':disabled') ? 1 : 0;
      h = this.hovers.get(el) ?? 0;
      h += (want - h) * Math.min(1, dt * 9);
      if (Math.abs(want - h) < 0.01) h = want; else this.hovering = true;
      if (want) this.hovering = true;
      this.hovers.set(el, h);
    }
    gl.uniform4f(this.u('glass', 'uRect'), r.left * k, r.top * k, r.width * k, r.height * k);
    gl.uniform4f(this.u('glass', 'uRadius'), radii[0] * k, radii[1] * k, radii[2] * k, radii[3] * k);
    gl.uniform4f(this.u('glass', 'uTint'), tint[0], tint[1], tint[2], tint[3]);
    gl.uniform1f(this.u('glass', 'uLift'), lift);
    gl.uniform1f(this.u('glass', 'uSpec'), spec);
    gl.uniform1f(this.u('glass', 'uHover'), h);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  /** Scissor to the folded section around el; false when el is clipped away or hidden. */
  private clip(el: Element, r: DOMRect, k: number) {
    const gl = this.gl;
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const clipper = el.closest(CLIP_SELECTOR);
    if (!clipper) { gl.disable(gl.SCISSOR_TEST); return true; }
    const c = clipper.getBoundingClientRect();
    const left = Math.max(r.left, c.left - CLIP_MARGIN), right = Math.min(r.right, c.right + CLIP_MARGIN);
    const top = Math.max(r.top, c.top - CLIP_MARGIN), bottom = Math.min(r.bottom, c.bottom + CLIP_MARGIN);
    if (right <= left || bottom <= top) return false;
    gl.enable(gl.SCISSOR_TEST);
    const x0 = Math.floor(left * k), y0 = Math.floor(this.height - bottom * k);
    gl.scissor(x0, y0, Math.ceil(right * k) - x0, Math.ceil(this.height - top * k) - y0);
    return true;
  }

  private drawText(k: number) {
    const gl = this.gl, dark = this.dark.matches;
    const items = Array.from(document.querySelectorAll(GLASS_TEXT_SELECTOR));
    if (!items.length) return;
    gl.useProgram(this.progs['text']);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.chain[2].tex);
    gl.uniform1i(this.u('text', 'uBlur'), 0);
    gl.uniform1i(this.u('text', 'uMask'), 1);
    gl.uniform2f(this.u('text', 'uScreen'), this.width, this.height);
    gl.uniform1f(this.u('text', 'uDpr'), this.dpr);
    gl.uniform1f(this.u('text', 'uMaxD'), TEXT_MAXD * this.dpr);
    gl.uniform1f(this.u('text', 'uShadow'), dark ? 0.18 : 0.1);
    gl.uniform1f(this.u('text', 'uLift'), dark ? 0.2 : 0.14);
    for (const el of items) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight) continue;
      const mask = this.textMask(el, r, k);
      if (!mask) continue;
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, mask.tex);
      gl.uniform4f(this.u('text', 'uRect'), r.left * k - mask.pad, r.top * k - mask.pad, mask.w, mask.h);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    gl.activeTexture(gl.TEXTURE0);
  }

  /** The element's text as coverage + inside/outside distance, cached until text, font or size change. */
  private textMask(el: Element, r: DOMRect, k: number): TextMask | null {
    const cs = getComputedStyle(el);
    const text = el.textContent?.trim() ?? '';
    const key = [text, cs.font, cs.letterSpacing, Math.round(r.width * k), Math.round(r.height * k)].join('|');
    const cached = this.masks.get(el);
    if (cached?.key === key) return cached;
    if (!text) return null;
    const pad = Math.ceil(TEXT_MAXD * this.dpr);
    const w = Math.ceil(r.width * k) + 2 * pad, h = Math.ceil(r.height * k) + 2 * pad;
    const c2 = document.createElement('canvas'); c2.width = w; c2.height = h;
    const ctx = c2.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    const size = parseFloat(cs.fontSize) * k;
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${size}px ${cs.fontFamily}`;
    const spacing = parseFloat(cs.letterSpacing);
    if ('letterSpacing' in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${(isNaN(spacing) ? 0 : spacing) * k}px`;
    // Where the DOM put the glyphs: the text's own box; its top is the font's ascent above the baseline.
    const range = document.createRange(); range.selectNodeContents(el);
    const tr = range.getBoundingClientRect();
    const metrics = ctx.measureText(text);
    ctx.fillStyle = '#fff'; ctx.textBaseline = 'alphabetic';
    ctx.fillText(text, pad + (tr.left - r.left) * k, pad + (tr.top - r.top) * k + metrics.fontBoundingBoxAscent);
    const alpha = ctx.getImageData(0, 0, w, h).data;
    const inside = new Float32Array(w * h), outside = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) {
      const on = alpha[i * 4 + 3] >= 128;
      inside[i] = on ? 1e10 : 0; outside[i] = on ? 0 : 1e10;
    }
    edt(inside, w, h); edt(outside, w, h);
    const maxd = TEXT_MAXD * this.dpr, out = new Uint8Array(w * h * 4);
    for (let i = 0; i < w * h; i++) {
      out[i * 4] = alpha[i * 4 + 3];
      out[i * 4 + 1] = Math.min(255, Math.round(Math.sqrt(inside[i]) / maxd * 255));
      out[i * 4 + 2] = Math.min(255, Math.round(Math.sqrt(outside[i]) / maxd * 255));
      out[i * 4 + 3] = 255;
    }
    const gl = this.gl;
    const tex = cached?.tex ?? gl.createTexture()!;
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, out);
    this.params(gl.LINEAR);
    gl.activeTexture(gl.TEXTURE0);
    const mask = { key, tex, w, h, pad };
    this.masks.set(el, mask);
    return mask;
  }
}

/** A CSS hex colour (#rgb or #rrggbb) as linear-free 0..1 sRGB components. */
function parseColor(value: string, fallback: number[]): number[] {
  const v = value.trim();
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v);
  if (!m) return fallback;
  const hex = m[1].length === 3 ? m[1].split('').map(c => c + c).join('') : m[1];
  return [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
}

/** Loads Apple's scene data from the control API (cached by the browser for a day). */
export async function loadSequoiaData(): Promise<SequoiaData> {
  const response = await fetch('/api/control/sequoia', { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Sequoia data unavailable (${response.status})`);
  const data = await response.json() as SequoiaData;
  if (data.version !== 1) throw new Error('Unknown Sequoia data version');
  return data;
}
