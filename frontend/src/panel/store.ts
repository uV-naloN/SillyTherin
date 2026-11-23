// Zustand store: the merged props and the UI state that used to live on
// window.__rcNotebook. The React root never unmounts now, so this only
// holds state that outside code (the shim) feeds in, or that several parts share.
import { create } from "zustand";
import { newer } from "@/panel/lib/newer";
import type { ActionResult, CallAction, NotebookProps } from "@/panel/types";

export interface Focus { id: string; start: number; end: number }

/** What the top bar needs to switch or create a workspace; the notebook registers it once loaded */
export interface ProjectControls {
  projectId: string;
  name: string;
  switchProject: (id: string) => Promise<unknown>;
  createProject: (name: string) => Promise<unknown>;
}

export interface PanelState {
  nb: NotebookProps | null;
  callAction: CallAction | null;
  tab: "notebook" | "datasets";
  /** cell id -> unsaved text */
  drafts: Record<string, string>;
  /** cell id with an active IME composition */
  composing: string | null;
  /** where the cursor should go when a new or inserted cell mounts */
  focus: Focus | null;
  /** note ids open for editing */
  editing: Record<string, boolean>;
  dsOpen: Record<string, boolean>;
  dsFilter: string;
  dsSort: "newest" | "name" | "size";
  projectControls: ProjectControls | null;
  setProps: (props: NotebookProps) => void;
  /** Merge an action reply that carries props; returns the reply */
  apply: (result: ActionResult) => ActionResult;
  setCallAction: (fn: CallAction) => void;
  setProjectControls: (controls: ProjectControls | null) => void;
  resetForProject: () => void;
}

type ProjectState = Pick<PanelState, "drafts" | "composing" | "focus" | "editing" | "dsOpen" | "dsFilter">;
type DataState = Omit<PanelState, "setProps" | "apply" | "setCallAction" | "setProjectControls" | "resetForProject">;

/** What a project starts with; new objects each time, so no two projects share one */
const freshProjectState = (): ProjectState => ({ drafts: {}, composing: null, focus: null, editing: {}, dsOpen: {}, dsFilter: "" });

const freshPanelState = (): DataState => ({ nb: null, callAction: null, tab: "notebook", ...freshProjectState(), dsSort: "newest", projectControls: null });

export const usePanel = create<PanelState>()((set, get) => ({
  ...freshPanelState(),

  setProps: (props) => {
    const prev = get().nb;
    if (prev && prev.project.id !== props.project.id) get().resetForProject();
    const next = newer(prev, props);
    if (next !== prev) set({ nb: next });
  },

  apply: (result) => {
    if (result && result.cells) {
      const prev = get().nb;
      let next = newer(prev, result as NotebookProps);
      if (result.error) next = { ...next, error: result.error };
      set({ nb: next });
    }
    return result;
  },

  setProjectControls: (controls) => {
    set({ projectControls: controls });
  },

  setCallAction: (fn) => {
    if (get().callAction !== fn) set({ callAction: fn });
  },

  resetForProject: () => set(freshProjectState()),
}));

/** Test helper: back to a fresh store */
export function resetStore() {
  usePanel.setState(freshPanelState());
}
