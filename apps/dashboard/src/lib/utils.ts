import { clsx, type ClassValue } from "clsx";
import { splitLongToH3Index } from "h3-js";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function formatConfidence(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export const MAP_STYLE =
  process.env.NEXT_PUBLIC_MAP_STYLE_URL ??
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

export const DEFAULT_CENTER: [number, number] = [73.8567, 18.5204];

/** Convert Python h3 uint64 int to H3 index string (h3-js v4). */
export function h3IntToString(cell: string | number): string {
  if (typeof cell === "string" && cell.length > 8) return cell;
  const value = typeof cell === "number" ? cell : Number(cell);
  const lower = value >>> 0;
  const upper = Math.floor(value / 4294967296) >>> 0;
  return splitLongToH3Index(lower, upper);
}

export function congestionColor(index: number): [number, number, number, number] {
  if (index >= 0.7) return [239, 68, 68, 220];
  if (index >= 0.5) return [249, 115, 22, 200];
  if (index >= 0.3) return [234, 179, 8, 180];
  return [34, 197, 94, 160];
}

export function cameraStatusColor(status: string): [number, number, number, number] {
  switch (status) {
    case "active":
      return [34, 197, 94, 220];
    case "degraded":
      return [234, 179, 8, 220];
    case "offline":
      return [148, 163, 184, 200];
    default:
      return [96, 165, 250, 220];
  }
}
