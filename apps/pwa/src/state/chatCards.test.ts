import { describe, expect, it } from "vitest";
import { mapTiles, projectPoint, tileUrl } from "./chatCards";
import type { MapCard } from "../api/client";

function jakartaCard(): MapCard {
  return {
    kind: "map",
    title: "Rute ke kantor",
    center: { lat: -6.2, lon: 106.8 },
    zoom: 15,
    markers: [{ lat: -6.2, lon: 106.8, label: "Kantor" }],
    route: [
      { lat: -6.2, lon: 106.8 },
      { lat: -6.21, lon: 106.81 },
    ],
  };
}

describe("tileUrl", () => {
  it("builds the OSM tile URL, wrapping x and clamping y", () => {
    expect(tileUrl(1, 0, 0)).toBe("https://tile.openstreetmap.org/1/0/0.png");
    expect(tileUrl(1, 2, 0)).toBe("https://tile.openstreetmap.org/1/0/0.png");
    expect(tileUrl(1, 0, 5)).toBe("https://tile.openstreetmap.org/1/0/1.png");
  });
});

describe("mapTiles", () => {
  it("covers the view around the centre with no gaps", () => {
    const tiles = mapTiles(jakartaCard(), 512, 512);
    // A 512x512 view is 2 tiles across when the centre lands on
    // a tile boundary and 3 when it does not.
    expect([4, 6, 9]).toContain(tiles.length);
    expect(tiles.every((t) => t.z === 15)).toBe(true);
    expect(tiles.every((t) => t.url.startsWith("https://tile.openstreetmap.org/15/"))).toBe(true);
    const xs = [...new Set(tiles.map((t) => t.leftPx))].sort((a, b) => a - b);
    const ys = [...new Set(tiles.map((t) => t.topPx))].sort((a, b) => a - b);
    expect(xs.every((v, i) => i === 0 || v - xs[i - 1] === 256)).toBe(true);
    expect(ys.every((v, i) => i === 0 || v - ys[i - 1] === 256)).toBe(true);
    expect(Math.min(...xs)).toBeLessThanOrEqual(0);
    expect(Math.max(...xs) + 256).toBeGreaterThanOrEqual(512);
    expect(Math.min(...ys)).toBeLessThanOrEqual(0);
    expect(Math.max(...ys) + 256).toBeGreaterThanOrEqual(512);
  });

  it("puts the centre pixel in the middle of the view", () => {
    const card = jakartaCard();
    const centre = projectPoint(card, { lat: -6.2, lon: 106.8 }, 512, 512);
    expect(centre).toEqual({ x: 256, y: 256 });
  });

  it("moves east with longitude and north with latitude", () => {
    const card = jakartaCard();
    const here = projectPoint(card, { lat: -6.2, lon: 106.8 }, 512, 512);
    const east = projectPoint(card, { lat: -6.2, lon: 106.81 }, 512, 512);
    const north = projectPoint(card, { lat: -6.19, lon: 106.8 }, 512, 512);
    expect(east.x).toBeGreaterThan(here.x);
    expect(east.y).toBeCloseTo(here.y);
    expect(north.y).toBeLessThan(here.y);
    expect(north.x).toBeCloseTo(here.x);
  });
});
