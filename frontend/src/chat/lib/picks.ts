// The model and effort last picked in each workspace, remembered in this browser.
import type { Picks } from "@/chat/lib/effortLevels";

export const PICKS_KEY = "sillytherin.picks";

type PicksByWorkspace = Record<string, Picks>;

export function readRememberedPicks(storage: Storage, workspaceId: string): Picks | null {
  const picks = readAll(storage)[workspaceId];
  return isPicks(picks) ? { model: picks.model, effort: picks.effort } : null;
}

export function rememberPicks(storage: Storage, workspaceId: string, picks: Picks): void {
  try {
    storage.setItem(PICKS_KEY, JSON.stringify({ ...readAll(storage), [workspaceId]: picks }));
  } catch {
    // not remembered: the picks still apply to this chat
  }
}

function readAll(storage: Storage): PicksByWorkspace {
  try {
    const parsed: unknown = JSON.parse(storage.getItem(PICKS_KEY) ?? "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as PicksByWorkspace) : {};
  } catch {
    return {};
  }
}

function isPicks(value: unknown): value is Picks {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.model === "string" && typeof candidate.effort === "string";
}
