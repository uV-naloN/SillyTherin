// Group 2 — dial renders
// Baseline: <EffortGauge fraction={1} />
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EffortGauge } from "@/chat/components/EffortGauge";

const renderDial = (fraction: number | null) => render(<EffortGauge fraction={fraction} />).container;
const part = (root: HTMLElement, name: string) => root.querySelector(`[data-part="${name}"]`) as SVGElement | null;

describe("Effort dial", () => {
  it("rotates the needle to the fraction", () => {
    // Then: needle style rotate(90deg)
    // Demonstrates: the needle shows the picked level
    const dial = renderDial(1);
    expect(part(dial, "needle")?.style.transform).toBe("rotate(90deg)");
  });

  it("paints the arc with the green to red gradient", () => {
    // Then: fill stroke uses a gradient with stops green, orange, red
    // Demonstrates: higher effort looks hotter
    const dial = renderDial(1);
    const gradient = dial.querySelector("linearGradient");
    const stopColors = Array.from(gradient?.querySelectorAll("stop") ?? []).map((stop) => stop.getAttribute("stop-color"));
    expect(stopColors).toEqual(["#22c55e", "#f59e0b", "#ef4444"]);
    expect(part(dial, "fill")?.getAttribute("stroke")).toBe(`url(#${gradient?.id})`);
  });

  it("greys the arc for model default", () => {
    // Changed: fraction null · Then: needle 0deg, dashed grey arc, no coloured fill
    // Demonstrates: "the model decides" looks different from any level
    const dial = renderDial(null);
    expect(part(dial, "needle")?.style.transform).toBe("rotate(0deg)");
    expect(part(dial, "default-arc")?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(part(dial, "fill")).toBeNull();
  });

  it("is hidden from screen readers", () => {
    // Then: root svg has aria-hidden="true"
    // Demonstrates: the button is announced only by its name "Effort: …"
    expect(renderDial(1).querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("turns off the needle animation for reduced motion", () => {
    // Then: needle has motion-reduce:transition-none
    // Demonstrates: people who ask for less motion get a jump, not a swing
    expect(part(renderDial(1), "needle")?.getAttribute("class")).toContain("motion-reduce:transition-none");
  });
});
