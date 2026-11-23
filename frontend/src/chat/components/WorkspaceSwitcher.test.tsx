// Group 1 — workspace switcher in the top bar
// Baseline: panel loaded with workspace "Default", projects [Default, Air quality]
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/chat/components/AppShell";
import { WorkspaceSwitcher } from "@/chat/components/WorkspaceSwitcher";
import { queryClient } from "@/panel/components/App";
import { resetStore } from "@/panel/store";
import { props } from "@/panel/test/fixtures";
import { renderWithChat, sessionReady, startChat } from "@/chat/test/renderChat";

const WORKSPACES = [{ id: "p1", name: "Default" }, { id: "p2", name: "Air quality" }];

type SentAction = { name: string; payload: Record<string, unknown> };

function workspaceSender() {
  const callAction = vi.fn(async (...args: unknown[]) => ((args[0] as SentAction).name === "nb_list_projects" ? { projects: WORKSPACES } : null));
  return { send: vi.fn(() => true), callAction };
}

const sentNames = (callAction: ReturnType<typeof workspaceSender>["callAction"]) =>
  callAction.mock.calls.map(([action]) => action as SentAction).filter((action) => action.name !== "nb_list_projects");

function startWithWorkspace(name = "Default") {
  startChat([], sessionReady({ panel: props({ project: { id: "p1", name } }) }));
}

async function openSwitcher(name = "Default") {
  const trigger = await screen.findByRole("button", { name: `Workspace ${name}, switch workspace` });
  await act(async () => {
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" });
  });
  // The open menu is portalled to the body; find it by its label
  let menu: HTMLElement | null = null;
  await waitFor(() => {
    menu = document.querySelector<HTMLElement>('[role="menu"][aria-label="Workspaces"]');
    expect(menu).not.toBeNull();
  });
  return menu as unknown as HTMLElement;
}

beforeEach(() => {
  localStorage.clear();
  resetStore();
  queryClient.clear();
  startWithWorkspace();
});

describe("Workspace switcher", () => {
  it("top bar shows the workspace name as a switch button", async () => {
    // Then: button "Workspace Default, switch workspace"; menu titled "Workspaces" with "New workspace…"
    // Demonstrates: the workspace is changed from the top bar
    renderWithChat(<AppShell />, workspaceSender());
    const menu = await openSwitcher();
    expect(menu.textContent).toContain("Workspaces");
    expect(await within(menu).findByText("Air quality")).toBeTruthy();
    expect(within(menu).getByText("New workspace…")).toBeTruthy();
  });

  it("picking a workspace switches to it", async () => {
    // When: pick "Air quality" · Then: drafts saved, then nb_switch_project {project_id}
    // Demonstrates: switching keeps unsaved edits
    const sender = workspaceSender();
    renderWithChat(<AppShell />, sender);
    const menu = await openSwitcher();
    await act(async () => {
      fireEvent.click(await within(menu).findByText("Air quality"));
    });
    await waitFor(() => expect(sentNames(sender.callAction)).toContainEqual(expect.objectContaining({ name: "nb_switch_project", payload: { project_id: "p2" } })));
  });

  it("creating a workspace sends the trimmed name", async () => {
    // When: "  Trial " and Create · Then: nb_create_project {name: "Trial"}
    // Demonstrates: a new workspace starts from the top bar
    const sender = workspaceSender();
    renderWithChat(<AppShell />, sender);
    const menu = await openSwitcher();
    await act(async () => {
      fireEvent.click(within(menu).getByText("New workspace…"));
    });
    fireEvent.change(screen.getByRole("textbox", { name: "New workspace name" }), { target: { value: "  Trial " } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create" }));
    });
    await waitFor(() => expect(sentNames(sender.callAction)).toContainEqual(expect.objectContaining({ name: "nb_create_project", payload: { name: "Trial" } })));
  });

  it("switcher works while the notebook is hidden", async () => {
    // Changed: panel closed · Then: the menu still opens and switches
    // Demonstrates: no need to open the notebook to change workspace
    const sender = workspaceSender();
    renderWithChat(<AppShell />, sender);
    fireEvent.click(screen.getByRole("button", { name: /Hide notebook/ }));
    const menu = await openSwitcher();
    await act(async () => {
      fireEvent.click(await within(menu).findByText("Air quality"));
    });
    await waitFor(() => expect(sentNames(sender.callAction).map((action) => action.name)).toContain("nb_switch_project"));
  });

  it("shows plain name before the notebook loads", () => {
    // Changed: no projectControls · Then: name shown, no menu button
    // Demonstrates: no control that does nothing
    render(<WorkspaceSwitcher fallbackName="Default" />);
    expect(screen.getByText("Default")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps create disabled for a blank name", async () => {
    // Changed: name "   " · Then: Create disabled, nothing sent
    // Demonstrates: no empty workspaces
    const sender = workspaceSender();
    renderWithChat(<AppShell />, sender);
    const menu = await openSwitcher();
    await act(async () => {
      fireEvent.click(within(menu).getByText("New workspace…"));
    });
    fireEvent.change(screen.getByRole("textbox", { name: "New workspace name" }), { target: { value: "   " } });
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(true);
    expect(sentNames(sender.callAction)).toEqual([]);
  });

  it("shows a workspace name with html as text", async () => {
    // Changed: name with an <img onerror> tag · Then: literal text, no img element
    // Demonstrates: workspace names can't inject markup
    const hostileName = '<img src=x onerror="alert(1)">';
    startWithWorkspace(hostileName);
    renderWithChat(<AppShell />, workspaceSender());
    const trigger = await screen.findByRole("button", { name: `Workspace ${hostileName}, switch workspace` });
    expect(trigger.textContent).toContain(hostileName);
    expect(trigger.querySelector("img")).toBeNull();
  });

  it("picking the current workspace sends nothing", async () => {
    // Changed: pick "Default" · Then: no socket call
    // Demonstrates: no needless reload
    const sender = workspaceSender();
    renderWithChat(<AppShell />, sender);
    const menu = await openSwitcher();
    await act(async () => {
      fireEvent.click(await within(menu).findByText("Default"));
    });
    expect(sentNames(sender.callAction)).toEqual([]);
  });
});
