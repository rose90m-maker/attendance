export function uid(prefix = 'id'): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e6).toString(36)}`;
}
export function clamp(v: number, mn: number, mx: number) { return Math.min(Math.max(v, mn), mx); }
export function snap(v: number, g: number) { return g <= 0 ? v : Math.round(v / g) * g; }
