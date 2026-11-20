/**
 * Rich-card helpers for the Chat screen (P-12): the slippy-map
 * tile maths for the map card, mirrored from the server module
 * `puspa/pwa/chat_cards.py` so both sides agree on which OSM
 * tiles to fetch and where markers and the route sit.
 *
 * The tiles load client-side from free OpenStreetMap tiles
 * (`tile.openstreetmap.org`, no API key). Honest limit: this
 * VM has no live browser to prove a tile really renders — the
 * verification here is the tile URL maths plus the rendered
 * template, not a loaded tile in a real browser.
 */

import type { MapCard, MapPoint } from "../api/client";

export const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_MAX_ZOOM = 19;
export const TILE_PX = 256;

export interface MapTile {
  z: number;
  x: number;
  y: number;
  url: string;
  leftPx: number;
  topPx: number;
}

export interface PixelPoint {
  x: number;
  y: number;
}

/** One OSM tile URL. `x` wraps around the world; `y` clamps to the valid row range. */
export function tileUrl(zoom: number, x: number, y: number): string {
  const tiles = 1 << zoom;
  const wrappedX = ((x % tiles) + tiles) % tiles;
  const clampedY = Math.max(0, Math.min(y, tiles - 1));
  return OSM_TILE_URL.replace("{z}", String(zoom))
    .replace("{x}", String(wrappedX))
    .replace("{y}", String(clampedY));
}

function lonToTileX(lon: number, zoom: number): number {
  return ((lon + 180) / 360) * (1 << zoom);
}

function latToTileY(lat: number, zoom: number): number {
  // Standard slippy-map formula (Mercator); the latitude is
  // clamped to the Mercator range so the poles cannot produce
  // an infinite tile row.
  const clamped = Math.max(Math.min(lat, 85.05112878), -85.05112878);
  const radians = (clamped * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(radians) + 1 / Math.cos(radians)) / Math.PI) / 2) * (1 << zoom);
}

/**
 * The OSM tiles covering a `widthPx` x `heightPx` view centred
 * on the card's centre, each with its pixel offset from the
 * view's top-left corner and its tile URL.
 */
export function mapTiles(card: MapCard, widthPx: number, heightPx: number): MapTile[] {
  const zoom = card.zoom;
  const centreX = lonToTileX(card.center.lon, zoom) * TILE_PX;
  const centreY = latToTileY(card.center.lat, zoom) * TILE_PX;
  const left = centreX - widthPx / 2;
  const top = centreY - heightPx / 2;
  const firstX = Math.floor(left / TILE_PX);
  const firstY = Math.floor(top / TILE_PX);
  const lastX = Math.floor((left + widthPx - 1) / TILE_PX);
  const lastY = Math.floor((top + heightPx - 1) / TILE_PX);
  const tiles: MapTile[] = [];
  for (let x = firstX; x <= lastX; x++) {
    for (let y = firstY; y <= lastY; y++) {
      tiles.push({
        z: zoom,
        x,
        y,
        url: tileUrl(zoom, x, y),
        leftPx: x * TILE_PX - left,
        topPx: y * TILE_PX - top,
      });
    }
  }
  return tiles;
}

/** Pixel position of one lat/lon inside the map view. */
export function projectPoint(
  card: MapCard,
  point: MapPoint,
  widthPx: number,
  heightPx: number,
): PixelPoint {
  const zoom = card.zoom;
  const centreX = lonToTileX(card.center.lon, zoom) * TILE_PX;
  const centreY = latToTileY(card.center.lat, zoom) * TILE_PX;
  return {
    x: lonToTileX(point.lon, zoom) * TILE_PX - (centreX - widthPx / 2),
    y: latToTileY(point.lat, zoom) * TILE_PX - (centreY - heightPx / 2),
  };
}
