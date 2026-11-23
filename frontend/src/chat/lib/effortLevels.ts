// Which Effort levels the picked model offers, where effort lands when the model changes,
// and how Show thinking follows the model.
import type { Picker, SettingSwitch } from "@/chat/events";

export const DEFAULT_EFFORT = "default";
const SHOW_THINKING_ID = "show_thinking";
// Same wording the server uses for the Settings row, so the row reads the same whichever side greyed it
const SHOW_THINKING_ON = "Show the model's reasoning above its answer.";
const SHOW_THINKING_UNSUPPORTED = "This model doesn't think before answering.";

export interface Picks {
  model: string;
  effort: string;
}

/** The Effort levels `modelId` offers; empty when it doesn't think or isn't in the list. */
export function effortLevelsOf(modelPicker: Picker | undefined, modelId: string): string[] {
  const option = modelPicker?.options.find((o) => o.id === modelId);
  return option?.effort_levels ?? [];
}

/** The Effort picker narrowed to the levels `modelId` offers; null when that model doesn't think. */
export function effortPickerFor(modelPicker: Picker | undefined, effortPicker: Picker | undefined, modelId: string): Picker | null {
  const levels = effortLevelsOf(modelPicker, modelId);
  if (!effortPicker || levels.length === 0) return null;
  return { ...effortPicker, options: effortPicker.options.filter((o) => levels.includes(o.id)) };
}

/** The effort to keep after switching model: the current one if the new model offers it, else "default". */
export function effortAfterModelChange(levels: string[], currentEffort: string): string {
  return levels.includes(currentEffort) ? currentEffort : DEFAULT_EFFORT;
}

/** The picks a new chat starts with: the remembered ones where still offered, else the current ones. */
export function restoredPicks(pickers: Picker[], remembered: Picks, current: Picks): Picks {
  const modelPicker = pickers.find((p) => p.id === "model");
  const isModelOffered = !!modelPicker?.options.some((o) => o.id === remembered.model);
  if (!isModelOffered) return current;
  const levels = effortLevelsOf(modelPicker, remembered.model);
  if (levels.length > 0) return { model: remembered.model, effort: effortAfterModelChange(levels, remembered.effort) };
  return { model: remembered.model, effort: current.effort };
}

/** Settings with Show thinking greyed or enabled for the picked model. */
export function settingsForModel(settings: SettingSwitch[], canThink: boolean): SettingSwitch[] {
  return settings.map((row) => {
    if (row.id !== SHOW_THINKING_ID) return row;
    if (!canThink) return { ...row, initial: false, disabled: true, description: SHOW_THINKING_UNSUPPORTED };
    // a row the server greyed for the previous model starts on, as a thinking model's row does
    const initial = row.disabled ? true : row.initial;
    return { ...row, initial, disabled: false, description: SHOW_THINKING_ON };
  });
}
