import { describe, expect, it } from "vitest";
import { activeFacts, normalizeFacts, proposals } from "./memory";

describe("memory store", () => {
  it("normalizes full fact objects", () => {
    const facts = normalizeFacts([
      { key: "city", value: "Tangerang", origin: "user_stated", status: "active" },
      { key: "likes spicy food", value: "", origin: "inferred", status: "proposal" },
    ]);
    expect(facts).toHaveLength(2);
    expect(activeFacts(facts)).toHaveLength(1);
    expect(proposals(facts)).toHaveLength(1);
    expect(proposals(facts)[0].key).toBe("likes spicy food");
  });

  it("accepts plain string rows without crashing", () => {
    const facts = normalizeFacts(["just a string", null, 42, { key: "", value: "" }]);
    expect(facts).toHaveLength(1);
    expect(facts[0].key).toBe("just a string");
  });

  it("returns empty for non-array input", () => {
    expect(normalizeFacts(undefined)).toEqual([]);
    expect(normalizeFacts("nope")).toEqual([]);
  });
});
