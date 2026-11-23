// Group 1 — level maps to needle position
// Baseline: levels [default, off, low, medium, high], picked high
import { describe, expect, it } from "vitest";
import { effortGaugeFraction, needleAngleDegrees } from "@/chat/lib/effortGauge";

const LEVELS = ["default", "off", "low", "medium", "high"];

describe("Effort dial position", () => {
  it("maps the strongest level to the far right", () => {
    // Then: fraction 1, angle +90
    // Demonstrates: High looks like full throttle
    const fraction = effortGaugeFraction(LEVELS, "high");
    expect(fraction).toBe(1);
    expect(needleAngleDegrees(fraction)).toBe(90);
  });

  it("maps off to the far left", () => {
    // Changed: picked off · Then: fraction 0, angle -90
    // Demonstrates: Off reads as no thinking
    const fraction = effortGaugeFraction(LEVELS, "off");
    expect(fraction).toBe(0);
    expect(needleAngleDegrees(fraction)).toBe(-90);
  });

  it("spaces middle levels evenly", () => {
    // Changed: picked low, then medium · Then: 1/3, then 2/3
    // Demonstrates: each step moves the needle the same amount
    expect(effortGaugeFraction(LEVELS, "low")).toBeCloseTo(1 / 3);
    expect(effortGaugeFraction(LEVELS, "medium")).toBeCloseTo(2 / 3);
  });

  it("orders by strength not menu order", () => {
    // Changed: levels listed [high, low, off] · Then: off 0, high 1
    // Demonstrates: the menu order never flips the dial
    const shuffled = ["high", "low", "off"];
    expect(effortGaugeFraction(shuffled, "off")).toBe(0);
    expect(effortGaugeFraction(shuffled, "high")).toBe(1);
  });

  it("returns null for model default", () => {
    // Changed: picked default · Then: null, angle 0
    // Demonstrates: default isn't faked as a level
    const fraction = effortGaugeFraction(LEVELS, "default");
    expect(fraction).toBeNull();
    expect(needleAngleDegrees(fraction)).toBe(0);
  });

  it("centres a single level without NaN", () => {
    // Changed: levels [high] · Then: 0.5
    // Demonstrates: no broken angle with one level
    expect(effortGaugeFraction(["high"], "high")).toBe(0.5);
  });

  it("treats an unknown picked id as model default", () => {
    // Changed: picked turbo · Then: null
    // Demonstrates: bad data never crashes the button
    expect(effortGaugeFraction(LEVELS, "turbo")).toBeNull();
  });

  it("ignores unknown level ids when ranking", () => {
    // Changed: levels add turbo · Then: high still 1
    // Demonstrates: stray config doesn't shift the dial
    expect(effortGaugeFraction([...LEVELS, "turbo"], "high")).toBe(1);
  });
});

describe("Max effort on the dial", () => {
  it("max sits at the far right of the dial", () => {
    // Changed: levels off…max · Then: max fraction 1
    // Demonstrates: Max reads as the strongest level
    const withMax = ["default", "off", "low", "medium", "high", "max"];
    expect(effortGaugeFraction(withMax, "max")).toBe(1);
    expect(effortGaugeFraction(withMax, "high")).toBeCloseTo(3 / 4);
  });
});
