import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Build-output test: the Vite build must have produced a dist entry
 * that the FastAPI app can serve. Run `npm run build` before
 * `npm test` (the build script runs tsc + vite build first).
 */
const distDir = join(__dirname, "..", "dist");
const indexPath = join(distDir, "index.html");

describe("build output", () => {
  it("dist/index.html exists", () => {
    expect(existsSync(indexPath)).toBe(true);
  });

  it("index.html loads the bundled entry and the manifest", () => {
    const html = readFileSync(indexPath, "utf-8");
    expect(html).toContain("/assets/");
    expect(html).toMatch(/<script[^>]+src="\/assets\/index-[^"]+\.js"/);
    expect(html).toContain("/manifest.json");
    expect(html).toContain('id="root"');
  });

  it("the bootstrap placeholder survives the build for server injection", () => {
    const html = readFileSync(indexPath, "utf-8");
    expect(html).toContain("<!--PUSPA_BOOTSTRAP-->");
  });
});
