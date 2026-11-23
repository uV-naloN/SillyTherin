// A code cell's prompts follow Jupyter: [n]: beside the code and beside a result, [*]: while waiting
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { mountPanel, panelRoot } from "@/panel/test/harness";
import { cell, props } from "@/panel/test/fixtures";

const outputPrompts = () => Array.from(panelRoot().querySelectorAll(".rcnb-outprompt")).map((prompt) => prompt.textContent);
const inputPrompt = () => panelRoot().querySelector(".rcnb-count-run")?.textContent;

describe("Code cell prompts", () => {
  it("result_gets_the_count_and_printed_text_does_not", async () => {
    await mountPanel(props({ cells: [cell({ execution_count: 3, outputs: [{ kind: "stdout", text: "hi" }, { kind: "result", text: "42" }] })] }));
    expect(inputPrompt()).toBe("[3]:");
    expect(outputPrompts()).toEqual(["", "[3]:"]);
  });

  it("queued_cell_shows_a_star", async () => {
    await mountPanel(props({ cells: [cell({ execution_count: 3, status: "queued" })] }));
    expect(inputPrompt()).toBe("[*]:");
  });
});

const runButton = () => screen.getByRole("button", { name: "Run cell" }) as HTMLButtonElement;

describe("Code cells look like Jupyter", () => {
  it("cell shows its prompt and run button", async () => {
    // Then: [1]: and a button named Run cell
    // Demonstrates: each cell runs from its own button
    await mountPanel(props({ cells: [cell({ execution_count: 1 })] }));
    expect(inputPrompt()).toBe("[1]:");
    expect(runButton().disabled).toBe(false);
  });

  it("focused cell is marked active", async () => {
    // When: focus the editor · Then: the block is marked active (blue bar)
    // Demonstrates: you can see which cell you're in
    await mountPanel(props({ cells: [cell({ id: "a" }), cell({ id: "b", source: "y = 2" })] }));
    const editor = panelRoot().querySelector('[data-cell-id="b"] .cm-content') as HTMLElement;
    editor.focus();
    expect(document.activeElement?.closest("[data-cell-id]")?.getAttribute("data-cell-id")).toBe("b");
    // The blue bar is drawn by the block that holds focus (.rcnb-block:focus-within in the panel styles)
    expect(document.activeElement?.closest(".rcnb-block")?.getAttribute("data-cell-id")).toBe("b");
  });

  it("run button shows a spinner while running", async () => {
    // Changed: running · Then: spinner, still named Run cell
    // Demonstrates: unchanged behaviour
    await mountPanel(props({ cells: [cell({ id: "a" })], running_cell_id: "a" }));
    expect(runButton().querySelector(".animate-spin")).toBeTruthy();
  });

  it("run button is disabled without docker", async () => {
    // Changed: canRun false · Then: disabled, tooltip "Docker isn't running"
    // Demonstrates: unchanged behaviour
    await mountPanel(props({ cells: [cell()], kernel: "unavailable" }));
    expect(runButton().disabled).toBe(true);
    expect(runButton().title).toBe("Docker isn't running");
  });
});
