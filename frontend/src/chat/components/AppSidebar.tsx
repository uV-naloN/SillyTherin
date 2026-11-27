import { PanelLeftClose, PanelLeftOpen, SlidersHorizontal, SquarePen, type LucideIcon } from "lucide-react";
import { useState } from "react";
import mark from "@/assets/favicon.svg";
import logoDark from "@/assets/logo_dark.svg";
import logoLight from "@/assets/logo_light.svg";

interface AppSidebarProps {
  onNewChat: () => void;
  onOpenSettings: () => void;
}

interface SidebarRowProps {
  label: string;
  Icon: LucideIcon;
  isCollapsed: boolean;
  onClick: () => void;
}

const ROW_CLASS =
  "flex h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-sm text-foreground transition-colors hover:bg-foreground/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const ICON_BUTTON_CLASS =
  "flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/[0.07] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function SidebarRow({ label, Icon, isCollapsed, onClick }: SidebarRowProps): JSX.Element {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className={ROW_CLASS}>
      <Icon aria-hidden className="size-4 shrink-0" />
      {!isCollapsed && <span className="truncate">{label}</span>}
    </button>
  );
}

function ExpandedHeader({ onCollapse }: { onCollapse: () => void }): JSX.Element {
  return (
    <div className="flex h-14 shrink-0 items-center justify-between pl-3.5 pr-2">
      <img src={logoLight} alt="SillyTherin" className="h-6 w-auto dark:hidden" />
      <img src={logoDark} alt="SillyTherin" className="hidden h-6 w-auto dark:block" />
      <button
        type="button"
        aria-label="Collapse sidebar"
        title="Collapse sidebar"
        aria-expanded
        onClick={onCollapse}
        className={ICON_BUTTON_CLASS}
      >
        <PanelLeftClose aria-hidden className="size-[18px]" />
      </button>
    </div>
  );
}

// The logo doubles as the expand control: hovering it reveals the expand icon in the same spot.
function CollapsedHeader({ onExpand }: { onExpand: () => void }): JSX.Element {
  return (
    <div className="flex h-14 shrink-0 items-center justify-center">
      <button
        type="button"
        aria-label="Expand sidebar"
        title="Expand sidebar"
        aria-expanded={false}
        onClick={onExpand}
        className={`group/expand ${ICON_BUTTON_CLASS}`}
      >
        <img src={mark} alt="" className="size-7 group-hover/expand:hidden group-focus-visible/expand:hidden" />
        <PanelLeftOpen aria-hidden className="hidden size-[18px] group-hover/expand:block group-focus-visible/expand:block" />
      </button>
    </div>
  );
}

export function AppSidebar({ onNewChat, onOpenSettings }: AppSidebarProps): JSX.Element {
  const [isCollapsed, setIsCollapsed] = useState(true);

  return (
    <aside
      aria-label="Workspace sidebar"
      data-collapsed={isCollapsed}
      className="row-span-2 flex min-h-0 w-[248px] flex-col bg-muted/40 transition-[width] duration-200 data-[collapsed=true]:w-[52px] motion-reduce:transition-none"
    >
      {isCollapsed ? <CollapsedHeader onExpand={() => setIsCollapsed(false)} /> : <ExpandedHeader onCollapse={() => setIsCollapsed(true)} />}
      <div className="grid gap-0.5 px-2">
        <SidebarRow label="New chat" Icon={SquarePen} isCollapsed={isCollapsed} onClick={onNewChat} />
      </div>
      <div className="mt-auto grid gap-0.5 p-2">
        <SidebarRow label="Settings" Icon={SlidersHorizontal} isCollapsed={isCollapsed} onClick={onOpenSettings} />
      </div>
    </aside>
  );
}
