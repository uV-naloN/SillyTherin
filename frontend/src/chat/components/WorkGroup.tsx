import { useId, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Answer } from "@/chat/components/Answer";
import type { WorkGroupView } from "@/chat/store";

export type WorkStatus = "running" | "waiting" | "finished" | "interrupted";
type StepStatus = "finished" | "failed" | "skipped" | "running";

const WORK_STATUS_LABELS: Record<WorkStatus, string> = {
  running: "Running",
  waiting: "Needs you",
  finished: "Finished",
  interrupted: "Interrupted",
};

const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  finished: "Done",
  failed: "Failed",
  skipped: "Skipped",
  running: "Running",
};

function resultStatus(output: string): StepStatus {
  if (output.startsWith("Failed: ")) return "failed";
  if (output.startsWith("Skipped: ") || output.endsWith("\n\nSkipped.")) return "skipped";
  return "finished";
}

function ActivityStep({ title, status, children }: { title: string; status: StepStatus; children: ReactNode }): JSX.Element {
  return (
    <li className="st-activity-step relative min-w-0" data-status={status}>
      <details className="group/step min-w-0">
        <summary className="flex cursor-pointer list-none items-baseline gap-2 rounded-md px-2 py-1.5 hover:bg-accent focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          <span className="min-w-0 flex-1 break-words font-medium">{title}</span>
          <span className="shrink-0 text-xs text-[color:var(--activity-step-color)]">{STEP_STATUS_LABELS[status]}</span>
          <ChevronDown className="size-3.5 shrink-0 self-center text-muted-foreground group-open/step:rotate-180" aria-hidden />
        </summary>
        <div className="mb-1.5 min-w-0 rounded-md bg-muted/40 px-3 py-2 text-muted-foreground [&_.st-code]:text-xs [&_p]:text-[13px]">
          {children}
        </div>
      </details>
    </li>
  );
}

export function WorkGroup({ group, status }: { group: WorkGroupView | null; status: WorkStatus }): JSX.Element {
  const [expandedByUser, setExpandedByUser] = useState<boolean | null>(null);
  const timelineId = useId();
  const stepCount = group?.steps.length ?? 0;
  const hasTimeline = Boolean(group?.thinking) || stepCount > 0;
  const isOpen = hasTimeline && (expandedByUser ?? status === "running");
  const isActive = status === "running" || status === "waiting";
  const lastStep = group?.steps[stepCount - 1];
  const currentLabel = lastStep && lastStep.title === group?.label ? "Working on the reply" : group?.label;
  const headline = status === "waiting"
    ? "Waiting for your decision"
    : status === "running" ? currentLabel ?? "Preparing reply" : "Activity";
  const stepSummary = stepCount === 1 ? "1 tool result" : `${stepCount} tool results`;
  const overview = group?.isDone ? group.label : stepCount > 0 ? stepSummary : null;
  const header = (
    <>
      <span className="st-activity-indicator size-2.5 shrink-0 rounded-full" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-semibold" aria-live={isActive ? "polite" : undefined}>{headline}</span>
        {overview && <span className="block text-xs text-muted-foreground">{overview}</span>}
      </span>
      <span className="st-activity-badge shrink-0 rounded-full px-2 py-0.5 text-xs font-medium">{WORK_STATUS_LABELS[status]}</span>
    </>
  );
  const headerStyle = "flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left";
  return (
    <section className="st-activity min-w-0 rounded-xl border bg-card" data-status={status} aria-label="Agent activity">
      {hasTimeline ? (
        <button
          type="button"
          aria-expanded={isOpen}
          aria-controls={timelineId}
          onClick={() => setExpandedByUser(!isOpen)}
          className={`${headerStyle} cursor-pointer hover:bg-accent/60 focus-visible:outline-ring`}
        >
          {header}
          <ChevronDown className={`size-4 shrink-0 text-muted-foreground ${isOpen ? "rotate-180" : ""}`} aria-hidden />
        </button>
      ) : (
        <div className={headerStyle}>{header}</div>
      )}
      <div id={timelineId} hidden={!isOpen}>
        {isOpen && group && (
          <ol aria-label="Activity timeline" className="space-y-0.5 border-t py-2 pl-8 pr-3 text-[13px]">
            {group.thinking && (
              <ActivityStep title="Thinking" status={status === "running" && group.label === "Thinking" ? "running" : "finished"}>
                <p className="whitespace-pre-wrap break-words">{group.thinking}</p>
              </ActivityStep>
            )}
            {group.steps.map((step, index) => (
              <ActivityStep key={index} title={step.title} status={resultStatus(step.output)}>
                {step.output ? <Answer text={step.output} /> : <p>No output.</p>}
              </ActivityStep>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
