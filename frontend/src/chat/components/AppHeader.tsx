// Workspace switcher, theme toggle, and notebook toggle.
import { Moon, PanelRight, Sun } from "lucide-react";
import { WorkspaceSwitcher } from "@/chat/components/WorkspaceSwitcher";
import type { Theme } from "@/chat/lib/theme";

export interface AppHeaderProps {
  projectName: string | null;
  theme: Theme;
  isNotebookOpen: boolean;
  onToggleTheme: () => void;
  onToggleNotebook: () => void;
}

const ICON_BUTTON = "inline-flex size-9 items-center justify-center rounded-lg shrink-0 text-muted-foreground hover:bg-accent hover:text-foreground";

export function AppHeader({ projectName, theme, isNotebookOpen, onToggleTheme, onToggleNotebook }: AppHeaderProps): JSX.Element {
  return (
    <header className="flex h-14 min-w-0 shrink-0 items-center gap-2 border-b px-2 sm:gap-3 sm:px-4">
      {projectName && <WorkspaceSwitcher fallbackName={projectName} />}
      <span className="flex-1" />
      <button type="button" aria-label="Switch light or dark theme" onClick={onToggleTheme} className={ICON_BUTTON}>
        {theme === "dark" ? <Sun className="size-[18px]" aria-hidden /> : <Moon className="size-[18px]" aria-hidden />}
      </button>
      {/* fixed width: the label changes, the buttons beside it must not move */}
      <button
        type="button"
        aria-pressed={isNotebookOpen}
        title="Ctrl+\"
        onClick={onToggleNotebook}
        className="inline-flex h-9 w-9 shrink-0 items-center sm:w-[9.75rem] justify-center gap-2 whitespace-nowrap rounded-lg border text-[13px] font-medium hover:bg-accent aria-pressed:border-transparent aria-pressed:bg-muted"
      >
        <PanelRight className="size-4" aria-hidden />
        <span className="sr-only sm:not-sr-only">{isNotebookOpen ? "Hide notebook" : "Notebook"}</span>
      </button>
    </header>
  );
}
