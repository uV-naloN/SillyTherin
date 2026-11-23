// The Effort dial: an arc filled green to red up to a needle that points at the picked level.
import { useId } from "react";
import { needleAngleDegrees } from "@/chat/lib/effortGauge";
import { cn } from "@/lib/utils";

const ARC_PATH = "M4 15 A8 8 0 0 1 20 15";
const ARC_LENGTH = Math.PI * 8;
const CALM_COLOR = "#22c55e";
const WARM_COLOR = "#f59e0b";
const HOT_COLOR = "#ef4444";

export interface EffortGaugeProps {
  /** 0 = weakest level, 1 = strongest; null = "model default" (needle straight up, grey dashed arc). */
  fraction: number | null;
  className?: string;
}

export function EffortGauge({ fraction, className }: EffortGaugeProps): JSX.Element {
  const gradientId = `effort-gradient-${useId().replace(/:/g, "")}`;
  const isModelDefault = fraction === null;
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={cn("overflow-visible", className)}>
      <defs>
        <linearGradient id={gradientId} x1="4" y1="0" x2="20" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={CALM_COLOR} />
          <stop offset="0.55" stopColor={WARM_COLOR} />
          <stop offset="1" stopColor={HOT_COLOR} />
        </linearGradient>
      </defs>
      {isModelDefault ? <ModelDefaultArc /> : <FilledArc fraction={fraction} gradientId={gradientId} />}
      <line
        data-part="needle"
        x1="12" y1="15" x2="12" y2="8.5"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round"
        className="origin-[12px_15px] transition-transform duration-300 ease-out motion-reduce:transition-none"
        style={{ transform: `rotate(${needleAngleDegrees(fraction)}deg)` }}
      />
      <circle cx="12" cy="15" r="1.6" fill="currentColor" />
    </svg>
  );
}

function ModelDefaultArc(): JSX.Element {
  return (
    <path data-part="default-arc" d={ARC_PATH} className="stroke-muted-foreground" strokeWidth="2" strokeLinecap="round" strokeDasharray="1.6 2.4" />
  );
}

function FilledArc({ fraction, gradientId }: { fraction: number; gradientId: string }): JSX.Element {
  return (
    <>
      <path d={ARC_PATH} className="stroke-border" strokeWidth="2" strokeLinecap="round" />
      <path
        data-part="fill"
        d={ARC_PATH}
        stroke={`url(#${gradientId})`}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={ARC_LENGTH}
        strokeDashoffset={ARC_LENGTH * (1 - fraction)}
        opacity={fraction === 0 ? 0 : 1}
        className="transition-[stroke-dashoffset] duration-300 ease-out motion-reduce:transition-none"
      />
    </>
  );
}
