import { describe, expect, it } from "vitest";
import { evaluateFormula, formatWidgetValue } from "./chatWidgets";

describe("evaluateFormula", () => {
  it("computes arithmetic with the Python precedence rules", () => {
    const values = { a: 10, b: 3 };
    expect(evaluateFormula("a + b * 2", values)).toBe(16);
    expect(evaluateFormula("(a - b) / 2", values)).toBe(3.5);
    expect(evaluateFormula("a % b", values)).toBe(1);
    expect(evaluateFormula("a ** 2", values)).toBe(100);
    expect(evaluateFormula("-a + abs(-b)", values)).toBe(-7);
    expect(evaluateFormula("min(a, b)", values)).toBe(3);
    expect(evaluateFormula("max(a, b)", values)).toBe(10);
    expect(evaluateFormula("round(a / b, 1)", values)).toBe(3.3);
  });

  it("matches Python on power precedence and right associativity", () => {
    expect(evaluateFormula("-2 ** 2", {})).toBe(-4);
    expect(evaluateFormula("2 ** 3 ** 2", {})).toBe(512);
    expect(evaluateFormula("2 ** -1", {})).toBe(0.5);
    expect(evaluateFormula("(-2) ** 2", {})).toBe(4);
  });

  it("refuses unknown inputs, division by zero, and code-shaped formulas", () => {
    expect(() => evaluateFormula("a + missing", { a: 1 })).toThrow("unknown input");
    expect(() => evaluateFormula("a / b", { a: 1, b: 0 })).toThrow("divides by zero");
    expect(() => evaluateFormula("a.b", { a: 1 })).toThrow();
    expect(() => evaluateFormula("a[0]", { a: 1 })).toThrow();
    expect(() => evaluateFormula("a +", { a: 1 })).toThrow();
    expect(() => evaluateFormula("eval(a)", { a: 1 })).toThrow(
      "formula calls must be min, max, abs, or round",
    );
  });

  it("formats values like the server", () => {
    expect(formatWidgetValue(5250000, "rupiah")).toBe("Rp5.250.000");
    expect(formatWidgetValue(12.5, "percent")).toBe("12.5%");
    expect(formatWidgetValue(42, "number")).toBe("42");
    expect(formatWidgetValue(3.333, "number")).toBe("3.33");
  });
});
