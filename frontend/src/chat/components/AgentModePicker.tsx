import { Shield, ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AgentMode } from "@/chat/events";

const MODE_LABELS: Record<AgentMode, string> = { manual: "Manual", auto: "Auto", free: "Free" };

export function AgentModePicker({ mode, disabled, onChange }: {
  mode: AgentMode;
  disabled: boolean;
  onChange: (mode: AgentMode) => void;
}): JSX.Element {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger disabled={disabled} aria-label={`Agent mode: ${MODE_LABELS[mode]}`} className="group inline-flex h-[34px] items-center gap-1.5 rounded-lg border px-2.5 text-[13px] font-medium hover:bg-accent">
        <Shield className="size-3.5" aria-hidden />
        {MODE_LABELS[mode]}
        <ChevronDown className="size-3.5 transition-transform duration-150 group-data-[state=open]:rotate-180 motion-reduce:transition-none" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="end" className="st-agent-mode-menu w-[22.5rem] max-w-[calc(100vw-2rem)]">
        <DropdownMenuLabel>Agent mode</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={mode}>
          {(Object.keys(MODE_LABELS) as AgentMode[]).map((option) => (
            <DropdownMenuRadioItem key={option} value={option} onSelect={() => onChange(option)}>{MODE_LABELS[option]}</DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
