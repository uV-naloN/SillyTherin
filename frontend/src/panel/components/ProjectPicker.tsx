// Workspace picker in the top bar. The list comes from useProjects (TanStack Query).
// Workspace names are React text only, never HTML.
import { useState } from "react";
import { Check, ChevronsUpDown, Folder, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useProjects } from "@/panel/lib/actions";

export interface ProjectPickerProps {
  name: string;
  current: string;
  onSwitch: (id: string) => void;
  onCreate: (name: string) => void;
}

export function ProjectPicker({ name, current, onSwitch, onCreate }: ProjectPickerProps) {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState<string | null>(null);
  const projects = useProjects(current, true).data || [];
  return (
    <DropdownMenu
      modal={false}
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setNewName(null);
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title="Switch workspace"
          aria-label={`Workspace ${name}, switch workspace`}
          className="inline-flex h-8 min-w-0 items-center gap-1.5 rounded-lg px-2 text-[13px] font-medium hover:bg-accent data-[state=open]:bg-accent"
        >
          <Folder className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{name}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent data-rc-overlay="project" className="rcnb-mcontent rcnb-projects" align="start" sideOffset={4} aria-label="Workspaces">
        <div className="rcnb-mhead">Workspaces</div>
        {!projects.length && <DropdownMenuItem disabled className="rcnb-mitem">Loading…</DropdownMenuItem>}
        {projects.map((p) => (
          <DropdownMenuItem key={p.id} data-rc-item={p.id} className="rcnb-mitem" onSelect={() => onSwitch(p.id)}>
            {p.id === current ? <Check /> : <span className="rcnb-mspacer" />}
            <span className="rcnb-mlabel">{p.name}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        {newName === null ? (
          <DropdownMenuItem
            data-rc-item="__new"
            className="rcnb-mitem"
            onSelect={(e) => {
              // Turn the item into a name field in place; the menu stays open
              e.preventDefault();
              setNewName("");
            }}
          >
            <Plus />
            <span>New workspace…</span>
          </DropdownMenuItem>
        ) : (
          <form
            className="rcnb-mnew"
            onSubmit={(e) => {
              e.preventDefault();
              const n = newName.trim();
              if (!n) return;
              setOpen(false);
              setNewName(null);
              onCreate(n);
            }}
          >
            <Input
              autoFocus
              data-rc-item="__name"
              className="rcnb-mnew-input"
              value={newName}
              maxLength={80}
              placeholder="Workspace name"
              aria-label="New workspace name"
              onChange={(e) => setNewName(e.target.value)}
              // Typing must not trigger the menu's type-to-select; Escape still closes it
              onKeyDown={(e) => e.key !== "Escape" && e.stopPropagation()}
            />
            <Button type="submit" size="sm" className="rcnb-mnew-btn" disabled={!newName.trim()}>Create</Button>
          </form>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
