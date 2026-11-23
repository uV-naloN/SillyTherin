// The workspace name in the top bar: a menu to switch or create a workspace once the notebook has loaded.
import { QueryClientProvider } from "@tanstack/react-query";
import { Folder } from "lucide-react";
import { queryClient } from "@/panel/components/App";
import { ProjectPicker } from "@/panel/components/ProjectPicker";
import { usePanel } from "@/panel/store";

export function WorkspaceSwitcher({ fallbackName }: { fallbackName: string }): JSX.Element {
  const controls = usePanel((state) => state.projectControls);
  if (!controls) return <WorkspaceName name={fallbackName} />;
  // The picker's project list query lives in the panel's client; the top bar sits outside the panel's provider
  return (
    <QueryClientProvider client={queryClient}>
      <ProjectPicker name={controls.name} current={controls.projectId} onSwitch={controls.switchProject} onCreate={controls.createProject} />
    </QueryClientProvider>
  );
}

function WorkspaceName({ name }: { name: string }): JSX.Element {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 px-2 text-[13px] font-medium">
      <Folder className="size-4 shrink-0" aria-hidden />
      <span className="truncate">{name}</span>
    </span>
  );
}
