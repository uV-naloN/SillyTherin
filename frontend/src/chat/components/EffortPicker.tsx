// The Effort button: a gauge dial; each click moves to the next level, wrapping round at the end.
import type { Picker } from "@/chat/events";
import { EffortGauge } from "@/chat/components/EffortGauge";
import { effortGaugeFraction } from "@/chat/lib/effortGauge";

export function EffortPicker({ picker, picked, onPick }: { picker: Picker; picked: string; onPick: (level: string) => void }): JSX.Element {
  const index = Math.max(0, picker.options.findIndex((o) => o.id === picked));
  const current = picker.options[index];
  const next = picker.options[(index + 1) % picker.options.length];
  const levelName = `Effort: ${current?.name ?? picked}`;
  const fraction = effortGaugeFraction(picker.options.map((o) => o.id), current?.id ?? picked);
  return (
    <button
      type="button"
      onClick={() => next && onPick(next.id)}
      aria-label={levelName}
      className="group relative inline-flex h-[34px] w-10 items-center justify-center rounded-lg hover:bg-accent focus-visible:outline focus-visible:outline-2"
    >
      <EffortGauge fraction={fraction} className="size-[30px]" />
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-foreground px-2 py-1 text-xs font-medium text-background opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
      >
        {levelName}
      </span>
    </button>
  );
}
