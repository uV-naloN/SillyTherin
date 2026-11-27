// Message box
// Baseline: Given an idle chat · When you type hi and press Enter
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/chat/components/AppShell";
import { Composer } from "@/chat/components/Composer";
import { useChat } from "@/chat/store";
import { GEMMA, renderWithChat, sessionReady, startChat } from "@/chat/test/renderChat";
import { PICKS_KEY, readRememberedPicks } from "@/chat/lib/picks";

const box = () => screen.getByRole("textbox", { name: "Message SillyTherin" }) as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const enter = (over: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(box(), { key: "Enter", ...over });

beforeEach(() => {
  localStorage.clear();
  startChat();
});

describe("Message box", () => {
  it("enter sends the message with the picks", () => {
    // Changed: —
    // Then: chat.send with hi, the picked model and effort; the box empties
    // Demonstrates: sending works
    const { sender } = renderWithChat(<AppShell />);
    type("hi");
    enter();
    expect(sender.send).toHaveBeenCalledWith({ type: "chat.send", text: "hi", model: "anthropic/claude-sonnet-4.5", effort: "high" });
    expect(box().value).toBe("");
    expect(useChat.getState().messages.map((m) => [m.role, m.text])).toEqual([["user", "hi"]]);
  });

  it("shift enter adds a line and an empty box sends nothing", () => {
    // Changed: Shift+Enter; Enter on spaces
    // Then: a new line; no event sent
    // Demonstrates: no accidental sends
    const onSend = vi.fn(() => true);
    render(<Composer isStreaming={false} onSend={onSend} onStop={vi.fn()} />);
    type("line one");
    enter({ shiftKey: true });
    type("   ");
    enter();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("while streaming the button stops the answer", () => {
    // Changed: an answer is streaming
    // Then: Stop shown; clicking sends chat.stop
    // Demonstrates: you can stop a long answer
    const onStop = vi.fn();
    render(<Composer isStreaming onSend={vi.fn(() => true)} onStop={onStop} />);
    expect(screen.queryByRole("button", { name: "Send message" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("the model search filters by name or id", () => {
    // Changed: type gem
    // Then: only matching models listed, each with what it can do; picking one shows its name on the button
    // Demonstrates: finding a model among hundreds is quick
    render(<Composer isStreaming={false} onSend={vi.fn(() => true)} onStop={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Claude Sonnet 4.5/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search models" }), { target: { value: "gem" } });
    const options = within(screen.getByRole("listbox", { name: "Models" })).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([`${GEMMA.name}${GEMMA.id}${GEMMA.detail}`]);
    fireEvent.click(options[0]);
    expect(useChat.getState().picked.model).toBe(GEMMA.id);
    expect(screen.getByRole("button", { name: /Gemma 4 31B/ })).toBeTruthy();
  });

  it("effort shows only for thinking models", () => {
    // Changed: pickers without Effort
    // Then: no Effort button
    // Demonstrates: no control that does nothing
    act(() => useChat.getState().apply({ type: "pickers.changed", pickers: useChat.getState().pickers.filter((p) => p.id === "model") }));
    render(<Composer isStreaming={false} onSend={vi.fn(() => true)} onStop={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Effort/ })).toBeNull();
  });

  it("clicking effort moves to the next level", () => {
    // Changed: click Effort at the last level
    // Then: the next level shows, wrapping round to the first; the next chat.send carries it
    // Demonstrates: effort changes in one click
    const { sender } = renderWithChat(<AppShell />);
    fireEvent.click(screen.getByRole("button", { name: "Effort: High" }));
    expect(screen.getByRole("button", { name: "Effort: Model default" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Effort: Model default" }));
    expect(screen.getByRole("button", { name: "Effort: Low" })).toBeTruthy();
    type("hi");
    enter();
    expect(sender.send).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chat.send", effort: "low" }));
  });

  it("clicking effort swings the needle to the next level", () => {
    // Changed: click Effort at High
    // Then: "Effort: Model default", needle rotate(0deg)
    // Demonstrates: the dial follows each click
    renderWithChat(<AppShell />);
    fireEvent.click(screen.getByRole("button", { name: "Effort: High" }));
    const button = screen.getByRole("button", { name: "Effort: Model default" });
    const needle = button.querySelector('[data-part="needle"]') as SVGElement;
    expect(needle.style.transform).toBe("rotate(0deg)");
  });

  it("shows only the dial with the level in a tooltip", () => {
    // Then: no visible text in the button, aria-label "Effort: High", tooltip "Effort: High" on hover
    // Demonstrates: the chat box stays compact without losing the level's name
    renderWithChat(<AppShell />);
    const button = screen.getByRole("button", { name: "Effort: High" });
    const tooltip = within(button).getByRole("tooltip");
    expect(tooltip.textContent).toBe("Effort: High");
    expect(tooltip.className).toContain("opacity-0");
    expect(tooltip.className).toContain("group-hover:opacity-100");
    expect(button.querySelector("svg")).toBeTruthy();
  });
});

// Group 3 — Effort and Show thinking follow the picked model
// Baseline: Claude (default/off/low/medium/high), GPT (default/low/medium/high), Plain (none); Claude picked at High
const CLAUDE = { id: "vendor/claude", name: "Claude", selected: true, detail: "thinks", effort_levels: ["default", "off", "low", "medium", "high"] };
const GPT = { id: "vendor/gpt", name: "GPT", selected: false, detail: "thinks", effort_levels: ["default", "low", "medium", "high"] };
const PLAIN = { id: "vendor/plain", name: "Plain", selected: false, detail: "", effort_levels: [] as string[] };
const LEVELS = [["default", "Model default"], ["off", "Off"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]];

function startOn(modelId: string, effort = "high") {
  const models = [CLAUDE, GPT, PLAIN].map((model) => ({ ...model, selected: model.id === modelId }));
  const efforts = LEVELS.map(([id, name]) => ({ id, name, selected: id === effort, detail: "" }));
  startChat([], sessionReady({ pickers: [{ id: "model", name: "Model", options: models }, { id: "effort", name: "Effort", options: efforts }] }));
}

function pickModel(fromName: string, toName: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(fromName) }));
  fireEvent.click(within(screen.getByRole("listbox", { name: "Models" })).getByRole("option", { name: new RegExp(toName) }));
}

function showThinkingSwitch(): HTMLButtonElement {
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(screen.getByRole("button", { name: "Answers" }));
  return document.querySelector('[aria-labelledby="setting-show_thinking"]') as HTMLButtonElement;
}

const effortButton = () => screen.queryByRole("button", { name: /^Effort:/ });

describe("Effort follows the picked model", () => {
  it("effort hides and show thinking greys for a model that cannot think", () => {
    // When: pick Plain · Then: no Effort button; Show thinking disabled with the no-thinking reason
    // Demonstrates: no control that does nothing
    startOn(CLAUDE.id);
    renderWithChat(<AppShell />);
    pickModel("Claude", "Plain");
    expect(effortButton()).toBeNull();
    const toggle = showThinkingSwitch();
    expect(toggle.disabled).toBe(true);
    expect(screen.getByText("This model doesn't think before answering.")).toBeTruthy();
  });

  it("effort shows and show thinking enables for a thinking model", () => {
    // Changed: start on Plain, pick Claude · Then: Effort button; Show thinking enabled
    // Demonstrates: Effort is there before the first message
    startOn(PLAIN.id, "default");
    renderWithChat(<AppShell />);
    expect(effortButton()).toBeNull();
    pickModel("Plain", "Claude");
    expect(effortButton()).toBeTruthy();
    expect(showThinkingSwitch().disabled).toBe(false);
  });

  it("effort offers only the picked model's levels", () => {
    // When: pick GPT · Then: clicks go Model default → Low → Medium → High
    // Demonstrates: only levels the model accepts
    startOn(CLAUDE.id, "default");
    renderWithChat(<AppShell />);
    pickModel("Claude", "GPT");
    const seen = [effortButton()?.getAttribute("aria-label")];
    for (let i = 0; i < 4; i++) {
      fireEvent.click(effortButton() as HTMLElement);
      seen.push(effortButton()?.getAttribute("aria-label"));
    }
    expect(seen).toEqual(["Effort: Model default", "Effort: Low", "Effort: Medium", "Effort: High", "Effort: Model default"]);
  });

  it("effort moves to default when the new model lacks it", () => {
    // Changed: at Off, pick GPT · Then: "Effort: Model default"
    // Demonstrates: never a level the model can't use
    startOn(CLAUDE.id, "off");
    renderWithChat(<AppShell />);
    pickModel("Claude", "GPT");
    expect(effortButton()?.getAttribute("aria-label")).toBe("Effort: Model default");
    expect(useChat.getState().picked.effort).toBe("default");
  });

  it("effort hidden when the picked model is not in the list", () => {
    // Changed: picked id not offered · Then: no Effort button
    // Demonstrates: no control that does nothing
    startOn(CLAUDE.id);
    act(() => useChat.getState().pick("model", "vendor/gone"));
    renderWithChat(<AppShell />);
    expect(effortButton()).toBeNull();
  });
});

// Group 5 — picks are remembered as you pick
describe("Picks are remembered", () => {
  it("picking a model and effort remembers them for this workspace", () => {
    // When: pick GPT, then Medium · Then: storage has them under this workspace
    // Demonstrates: the next chat in this workspace starts here
    startOn(CLAUDE.id, "default");
    renderWithChat(<AppShell />);
    pickModel("Claude", "GPT");
    fireEvent.click(effortButton() as HTMLElement);
    fireEvent.click(effortButton() as HTMLElement);
    const workspaceId = useChat.getState().panel?.project.id as string;
    expect(readRememberedPicks(localStorage, workspaceId)).toEqual({ model: GPT.id, effort: "medium" });
  });

  it("nothing is remembered before the workspace is known", () => {
    // Changed: no panel yet · Then: storage unchanged
    // Demonstrates: picks never land under a wrong workspace
    startOn(CLAUDE.id, "default");
    act(() => useChat.setState({ panel: null }));
    render(<Composer isStreaming={false} onSend={vi.fn(() => true)} onStop={vi.fn()} />);
    pickModel("Claude", "GPT");
    expect(localStorage.getItem(PICKS_KEY)).toBeNull();
  });
});

describe("Agent mode picker", () => {
  const modeButton = (mode = "Auto") => screen.getByRole<HTMLButtonElement>("button", { name: `Agent mode: ${mode}` });

  const chooseFree = () => {
    fireEvent.pointerDown(modeButton(), { button: 0 });
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Free" }));
  };

  it("shows Auto and the three choices", () => {
    renderWithChat(<AppShell />);
    expect(screen.getByRole("button", { name: "Agent mode: Auto" })).toBeTruthy();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Agent mode: Auto" }), { button: 0 });
    expect(screen.getByRole("menuitemradio", { name: "Manual" })).toBeTruthy();
    expect(screen.getByRole("menuitemradio", { name: "Auto" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("menuitemradio", { name: "Free" })).toBeTruthy();
  });
  it("selects Manual by keyboard", async () => {
    const { sender } = renderWithChat(<AppShell />);
    const trigger = modeButton();
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const manual = screen.getByRole("menuitemradio", { name: "Manual" });
    expect(document.activeElement).toBe(manual);
    fireEvent.keyDown(manual, { key: "Enter" });

    expect(sender.send).toHaveBeenCalledTimes(1);
    expect(sender.send).toHaveBeenCalledWith({ type: "agent_mode.set", mode: "manual" });
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    act(() => useChat.getState().apply({ type: "agent_mode.changed", mode: "manual" }));
    expect(modeButton("Manual")).toBeTruthy();
  });

  it("selects Free by pointer", () => {
    const { sender } = renderWithChat(<AppShell />);
    chooseFree();
    expect(sender.send).toHaveBeenCalledWith({ type: "agent_mode.set", mode: "free" });
    act(() => useChat.getState().apply({ type: "agent_mode.changed", mode: "free" }));
    fireEvent.pointerDown(modeButton("Free"), { button: 0 });
    expect(screen.getByRole("menuitemradio", { name: "Free", checked: true })).toBeTruthy();
  });

  it("restores mode on ready", () => {
    renderWithChat(<AppShell />);
    act(() => useChat.getState().apply(sessionReady({ agent_mode: "manual" })));
    fireEvent.pointerDown(modeButton("Manual"), { button: 0 });
    expect(screen.getByRole("menuitemradio", { name: "Manual", checked: true })).toBeTruthy();
  });

  it("resets mode with a new session", () => {
    startChat([], sessionReady({ agent_mode: "free" }));
    renderWithChat(<AppShell />);
    act(() => useChat.getState().apply({ type: "chat.closed" }));
    expect(modeButton()).toBeTruthy();
    act(() => useChat.getState().apply(sessionReady({ chat_id: "replacement" })));
    expect(modeButton()).toBeTruthy();
  });

  it("preserves mode across project switches", () => {
    startChat([], sessionReady({ agent_mode: "manual" }));
    renderWithChat(<AppShell />);
    const { chatId, panel } = useChat.getState();
    act(() => useChat.getState().apply({
      type: "panel.project_changed",
      props: { ...panel!, project: { id: "other-project", name: "Other project" } },
    }));
    expect(useChat.getState().chatId).toBe(chatId);
    expect(modeButton("Manual")).toBeTruthy();
  });

  it("defaults to Auto on older ready payload", () => {
    startChat([], sessionReady({ agent_mode: undefined }));
    renderWithChat(<AppShell />);
    expect(modeButton()).toBeTruthy();
  });

  it("dismisses the menu without selecting", async () => {
    const { sender } = renderWithChat(<AppShell />);
    const trigger = modeButton();
    fireEvent.pointerDown(trigger, { button: 0 });
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(sender.send).not.toHaveBeenCalled();
  });

  it("cannot change mode while offline", () => {
    startChat([], sessionReady({ agent_mode: "manual" }));
    useChat.getState().setStatus("reconnecting");
    const { sender } = renderWithChat(<AppShell />);
    const trigger = modeButton("Manual");
    expect(trigger.disabled).toBe(true);
    fireEvent.pointerDown(trigger, { button: 0 });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(sender.send).not.toHaveBeenCalled();
  });

  it("retains confirmed mode on rejected write", () => {
    renderWithChat(<AppShell />);
    chooseFree();
    act(() => useChat.getState().apply({ type: "error", code: "BAD_EVENT", message: "Not a valid event." }));
    expect(screen.getByText("Not a valid event.")).toBeTruthy();
    expect(modeButton()).toBeTruthy();
    expect(useChat.getState().messages).toEqual([]);
  });

  it("retains mode until acknowledgement", () => {
    renderWithChat(<AppShell />);
    chooseFree();
    expect(modeButton()).toBeTruthy();
    act(() => useChat.getState().apply({ type: "agent_mode.changed", mode: "free" }));
    expect(modeButton("Free")).toBeTruthy();
  });
});
