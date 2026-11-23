// Group 2 — remembered picks per workspace
// Baseline: storage {"p1":{"model":"vendor/plain","effort":"low"}}
import { beforeEach, describe, expect, it } from "vitest";
import { PICKS_KEY, readRememberedPicks, rememberPicks } from "@/chat/lib/picks";

const BASELINE = { p1: { model: "vendor/plain", effort: "low" } };

function blockedStorage(): Storage {
  const fail = () => {
    throw new Error("blocked");
  };
  return { length: 0, clear: fail, getItem: fail, key: fail, removeItem: fail, setItem: fail };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(PICKS_KEY, JSON.stringify(BASELINE));
});

describe("Remembered picks", () => {
  it("reads the picks for a workspace", () => {
    // Then: {model: "vendor/plain", effort: "low"} for p1
    // Demonstrates: a workspace gets back what was picked in it
    expect(readRememberedPicks(localStorage, "p1")).toEqual({ model: "vendor/plain", effort: "low" });
  });

  it("remembers picks per workspace without touching others", () => {
    // When: remember p2 · Then: p1 unchanged, p2 stored
    // Demonstrates: workspaces don't overwrite each other
    rememberPicks(localStorage, "p2", { model: "vendor/gpt", effort: "high" });
    expect(readRememberedPicks(localStorage, "p1")).toEqual({ model: "vendor/plain", effort: "low" });
    expect(readRememberedPicks(localStorage, "p2")).toEqual({ model: "vendor/gpt", effort: "high" });
  });

  it("ignores junk in storage", () => {
    // Changed: value "{oops" · Then: null, no throw
    // Demonstrates: a bad value never breaks the page
    localStorage.setItem(PICKS_KEY, "{oops");
    expect(readRememberedPicks(localStorage, "p1")).toBeNull();
  });

  it("ignores the wrong shape", () => {
    // Changed: {"p1":{"model":5}} · Then: null
    // Demonstrates: only real ids are used
    localStorage.setItem(PICKS_KEY, JSON.stringify({ p1: { model: 5 } }));
    expect(readRememberedPicks(localStorage, "p1")).toBeNull();
  });

  it("works when storage is blocked", () => {
    // Changed: getItem and setItem throw · Then: null, and remembering doesn't throw
    // Demonstrates: private windows work
    const storage = blockedStorage();
    expect(readRememberedPicks(storage, "p1")).toBeNull();
    expect(() => rememberPicks(storage, "p1", { model: "vendor/gpt", effort: "high" })).not.toThrow();
  });

  it("returns nothing for a workspace with no picks", () => {
    // Changed: read p9 · Then: null
    // Demonstrates: new workspaces start at the defaults
    expect(readRememberedPicks(localStorage, "p9")).toBeNull();
  });
});
