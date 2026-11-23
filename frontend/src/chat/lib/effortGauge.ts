// Where the Effort dial's needle points for the picked thinking level.

// Weakest to strongest, so the dial follows how hard the model thinks, not the menu order.
export const EFFORT_STRENGTH_ORDER: readonly string[] = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

export const NEEDLE_LEFT_DEGREES = -90;
export const NEEDLE_SWEEP_DEGREES = 180;
const SINGLE_LEVEL_FRACTION = 0.5;

/** 0 = weakest configured level, 1 = strongest; null = "model default" or an unknown level. */
export function effortGaugeFraction(levelIds: string[], pickedId: string): number | null {
  const rankedLevels = EFFORT_STRENGTH_ORDER.filter((level) => levelIds.includes(level));
  const rank = rankedLevels.indexOf(pickedId);
  if (rank < 0) return null;
  if (rankedLevels.length === 1) return SINGLE_LEVEL_FRACTION;
  return rank / (rankedLevels.length - 1);
}

/** Needle rotation in degrees: -90 (left) to +90 (right); 0 (straight up) for "model default". */
export function needleAngleDegrees(fraction: number | null): number {
  if (fraction === null) return 0;
  return NEEDLE_LEFT_DEGREES + NEEDLE_SWEEP_DEGREES * fraction;
}
