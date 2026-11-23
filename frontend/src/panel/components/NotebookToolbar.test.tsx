// Group 2 — panel heading and toolbar
// Baseline: notebook tab open, kernel idle, all saved
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mountPanel, panelRoot } from "@/panel/test/harness";
import { cell, props } from "@/panel/test/fixtures";
import { usePanel } from "@/panel/store";

const toolbar = () => screen.getByRole("toolbar", { name: "Notebook actions" });

async function typeUnsavedEdit() {
  await act(async () => {
    usePanel.setState({ drafts: { a: "print(2)" } });
  });
}

describe("Panel heading and toolbar", () => {
  it("panel has no project heading", async () => {
    // Then: no "Project notebook" text; the tabs come first
    // Demonstrates: the panel's space goes to the cells
    await mountPanel(props({ cells: [cell()] }));
    expect(panelRoot().textContent).not.toContain("Project notebook");
    expect(panelRoot().querySelector("h1")).toBeNull();
    const firstInteractive = panelRoot().querySelector("button, [role='tab']");
    expect(firstInteractive?.getAttribute("role")).toBe("tab");
  });

  it("toolbar shows icon buttons with their names", async () => {
    // Then: Run all, Stop, Restart kernel, Clear all outputs; no Find button; no visible label text
    // Demonstrates: a Jupyter-like toolbar that screen readers can still name
    await mountPanel(props({ cells: [cell()] }));
    const names = within(toolbar()).getAllByRole("button").map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual(["Run all", "Stop", "Restart kernel", "Clear all outputs"]);
    for (const button of within(toolbar()).getAllByRole("button")) expect(button.textContent).toBe("");
  });

  it("toolbar shows the save badge where the kernel name was", async () => {
    // Then: "Saved" at the right end, before the kernel dot
    // Demonstrates: save state stays visible without the heading
    const savesFine = vi.fn(async (action: { name: string }) => (action.name === "nb_list_projects" ? { projects: [] } : {}));
    await mountPanel(props({ cells: [cell()] }), savesFine);
    await typeUnsavedEdit();
    within(toolbar()).getByRole("button", { name: "Run all" }).focus();
    await act(async () => {
      fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    });
    await waitFor(() => expect(toolbar().querySelector(".rcnb-save")?.textContent).toBe("Saved"));
    const children = Array.from(toolbar().children);
    expect(children.indexOf(toolbar().querySelector(".rcnb-save") as Element)).toBe(children.length - 2);
  });

  it("kernel status is a dot with its text as tooltip", async () => {
    // Then: last toolbar child; title and accessible name "Ready"
    // Demonstrates: kernel state is still readable
    await mountPanel(props({ kernel: "idle" }));
    const status = toolbar().lastElementChild as HTMLElement;
    expect(status.getAttribute("title")).toBe("Ready");
    expect(status.getAttribute("aria-label")).toBe("Ready");
    expect(status.querySelector(".rcnb-dot")).toBeTruthy();
  });

  it("shows unsaved while edits wait", async () => {
    // Changed: a draft pending · Then: badge "Unsaved"
    // Demonstrates: save state isn't lost with the heading
    await mountPanel(props({ cells: [cell()] }));
    await typeUnsavedEdit();
    expect(toolbar().querySelector(".rcnb-save")?.textContent).toBe("Unsaved");
  });

  it("disables run all while busy", async () => {
    // Changed: runner busy · Then: Run all disabled
    // Demonstrates: behaviour unchanged
    const neverAnswers = vi.fn((action: { name: string }) => (action.name === "nb_run_all" ? new Promise(() => {}) : Promise.resolve(null)));
    await mountPanel(props({ cells: [cell()] }), neverAnswers);
    const runAll = within(toolbar()).getByRole("button", { name: "Run all" }) as HTMLButtonElement;
    await act(async () => {
      fireEvent.click(runAll);
    });
    await waitFor(() => expect(runAll.disabled).toBe(true));
  });
});
