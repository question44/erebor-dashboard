// Types for the parts of vendor/liquid-glass.js the dashboard uses.
export interface GlassOptions {
  x?: number; y?: number; width?: number; height?: number; radius?: number;
  refraction?: number; bezel?: number; curvature?: number; ior?: number; invert?: boolean;
  chroma?: number; blur?: number; specular?: number; specularWidth?: number; lightAngle?: number;
  mapScale?: number; mode?: 'content' | 'backdrop'; clip?: boolean; fit?: boolean; sync?: boolean; hidden?: boolean;
}
export interface Glass { update(options: GlassOptions): void; destroy(): void; }
export function createGlass(target: HTMLElement, options?: GlassOptions): Glass;
export interface Spring { set(next: number, options?: { immediate?: boolean }): void; }
export function createSpring(initial: number, onUpdate: (value: number, velocity: number) => void,
  options?: { stiffness?: number; damping?: number; mass?: number }): Spring;
export const reducedMotion: MediaQueryList;
