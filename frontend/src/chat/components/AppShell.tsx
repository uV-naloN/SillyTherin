// The layout: header, then the chat and the notebook panel side by side. With the notebook open
// the panel takes the room and the chat is a side column; closed, the page is a full-width chat.
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { AppHeader } from "@/chat/components/AppHeader";
import { AppSidebar } from "@/chat/components/AppSidebar";
import { Composer } from "@/chat/components/Composer";
import { ConnectionBar } from "@/chat/components/ConnectionBar";
import { KaggleDatasetsWindow } from "@/chat/components/KaggleDatasetsWindow";
import { useKaggleDownloads } from "@/chat/components/useKaggleDownloads";
import { KaggleSignInDialog } from "@/chat/components/KaggleSignInDialog";
import { MessageList } from "@/chat/components/MessageList";
import { PanelDivider } from "@/chat/components/PanelDivider";
import { PromptCard } from "@/chat/components/PromptCard";
import { KAGGLE_SWITCH_ID, SettingsDialog } from "@/chat/components/SettingsDialog";
import { StartScreen } from "@/chat/components/StartScreen";
import { Toasts } from "@/chat/components/Toasts";
import { useConnection } from "@/chat/connectionContext";
import { clampChatWidth, isNotebookOpen, isToggleShortcut, readChatWidth, rememberChatWidth, rememberNotebookOpen } from "@/chat/lib/layout";
import { applyTheme, readTheme, rememberTheme, type Theme } from "@/chat/lib/theme";
import { effortLevelsOf, settingsForModel } from "@/chat/lib/effortLevels";
import { readRememberedPicks } from "@/chat/lib/picks";
import { slashCommandsFor } from "@/chat/lib/slashCommands";
import { useChat, type PromptView } from "@/chat/store";
import { NotebookPanel } from "@/panel/NotebookPanel";

const prefersDark = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;

export function AppShell(): JSX.Element {
  const { send, callAction } = useConnection();
  const messages = useChat((s) => s.messages);
  const notice = useChat((s) => s.notice);
  const panel = useChat((s) => s.panel);
  const status = useChat((s) => s.status);
  const settings = useChat((s) => s.settings);
  const kaggle = useChat((s) => s.kaggle);
  const kagglePrompts = useChat((s) => s.kagglePrompts);
  const kaggleGeneration = useChat((s) => s.kaggleGeneration);
  const kaggleDownloads = useKaggleDownloads({ callAction, panel, generation: kaggleGeneration, isOnline: status === "open" });
  const picked = useChat((s) => s.picked);
  const pickers = useChat((s) => s.pickers);
  const chatId = useChat((s) => s.chatId);
  const workspaceId = panel?.project.id ?? null;
  const canThink = effortLevelsOf(pickers.find((p) => p.id === "model"), picked.model).length > 0;
  const [isOpen, setIsOpen] = useState(() => isNotebookOpen(window.localStorage));
  const [theme, setTheme] = useState<Theme>(() => readTheme(window.localStorage, prefersDark()));
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isKaggleWindowOpen, setIsKaggleWindowOpen] = useState(false);
  // the pop-up, not the switch: the switch flips on the click, before Kaggle has said yes
  const hasKaggleSignInFinished = kaggle?.state === "connected";
  const commands = useMemo(() => slashCommandsFor({ showKaggleDatasets: () => setIsKaggleWindowOpen(true) }), []);
  const row = useRef<HTMLDivElement>(null);
  const aside = useRef<HTMLElement>(null);
  const rowWidth = () => row.current?.clientWidth || window.innerWidth;
  const [rowSize, setRowSize] = useState(() => window.innerWidth);
  const [chatWidth, setChatWidth] = useState(() => clampChatWidth(readChatWidth(window.localStorage), window.innerWidth));
  const [isResizing, setIsResizing] = useState(false);
  const resizeChat = (width: number) => setChatWidth(clampChatWidth(width, rowWidth()));
  const rememberWidth = () => setChatWidth((width) => {
    rememberChatWidth(window.localStorage, width);
    return width;
  });
  const isStreaming = messages.some((m) => m.role === "assistant" && m.isStreaming);
  const hasStarted = messages.length > 0 || notice !== null;

  useEffect(() => applyTheme(document.documentElement, theme), [theme]);
  // a chat with no messages yet starts on what was last picked in this workspace
  useEffect(() => {
    if (!workspaceId || useChat.getState().messages.length > 0) return;
    const remembered = readRememberedPicks(window.localStorage, workspaceId);
    if (remembered) useChat.getState().restorePicks(remembered);
  }, [chatId, workspaceId]);
  // a narrower window re-applies the limits, so the notebook never drops below its minimum
  useEffect(() => {
    const fit = () => {
      setRowSize(rowWidth());
      setChatWidth((width) => clampChatWidth(width, rowWidth()));
    };
    fit();
    if (typeof ResizeObserver === "undefined" || !row.current) {
      window.addEventListener("resize", fit);
      return () => window.removeEventListener("resize", fit);
    }
    const observer = new ResizeObserver(fit);
    observer.observe(row.current);
    return () => observer.disconnect();
  }, []);
  // a closed notebook can't be reached by Tab or a screen reader while it slides away
  useEffect(() => {
    aside.current?.toggleAttribute("inert", !isOpen);
  }, [isOpen]);

  const toggleNotebook = useCallback(() => {
    setIsOpen((wasOpen) => {
      rememberNotebookOpen(window.localStorage, !wasOpen);
      return !wasOpen;
    });
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isToggleShortcut(event)) return;
      event.preventDefault();
      toggleNotebook();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleNotebook]);

  const changeTheme = (next: Theme) => {
    rememberTheme(window.localStorage, next);
    setTheme(next);
  };
  const sendMessage = (text: string) => {
    const isSent = send({ type: "chat.send", text, model: picked.model, effort: picked.effort });
    if (isSent) useChat.getState().sent(text);
    return isSent;
  };
  const changeSetting = (switchId: string, value: boolean) => {
    if (send({ type: "settings.update", values: { [switchId]: value } })) useChat.getState().setSetting(switchId, value);
  };

  return (
    <div className="flex h-full flex-col bg-background text-foreground" data-notebook={isOpen ? "open" : "closed"}>
      <ConnectionBar status={status} />
      {kaggle && <KaggleSignInDialog popup={kaggle} onClose={() => useChat.getState().closeKaggle()} />}
      {isKaggleWindowOpen && (
        <KaggleDatasetsWindow
          callAction={callAction}
          downloads={kaggleDownloads}
          isPaused={kagglePrompts.length > 0}
          connectionGeneration={kaggleGeneration}
          isOnline={status === "open"}
          hasSignInFinished={hasKaggleSignInFinished}
          onConnect={() => changeSetting(KAGGLE_SWITCH_ID, true)}
          onClose={() => setIsKaggleWindowOpen(false)}
        />
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[auto_minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)]">
        <AppSidebar onNewChat={() => window.location.reload()} onOpenSettings={() => setIsSettingsOpen(true)} />
        <AppHeader
          projectName={panel?.project.name ?? null}
          theme={theme}
          isNotebookOpen={isOpen}
          onToggleTheme={() => changeTheme(theme === "dark" ? "light" : "dark")}
          onToggleNotebook={toggleNotebook}
        />
        <div ref={row} className="flex min-h-0 flex-1 overflow-hidden max-md:flex-col">
          <main
            aria-label="Chat"
            data-width={isOpen ? "narrow" : "full"}
            style={{ "--chat-width": `${chatWidth}px` } as CSSProperties}
            className={[
              "flex min-h-0 min-w-0 flex-col max-md:flex-1",
              isOpen ? "md:w-[var(--chat-width)] md:shrink-0" : "md:w-full md:shrink-0",
              isResizing ? "" : "transition-[width] duration-300 ease-out motion-reduce:transition-none",
            ].join(" ")}
          >
            {hasStarted ? <MessageList /> : <StartScreen theme={theme} />}
            {kagglePrompts.length > 0 && <KaggleChatPrompts prompts={kagglePrompts} />}
            <Composer
              isStreaming={isStreaming}
              onSend={sendMessage}
              onStop={() => send({ type: "chat.stop" })}
              commands={commands}
              isConnected={status === "open"}
              onModeChange={(mode) => send({ type: "agent_mode.set", mode })}
            />
          </main>
          {isOpen && (
            <PanelDivider
              width={chatWidth}
              maxWidth={clampChatWidth(Number.MAX_SAFE_INTEGER, rowSize)}
              rowLeft={() => row.current?.getBoundingClientRect().left ?? 0}
              onResize={resizeChat}
              onResizeDone={rememberWidth}
              onDraggingChange={setIsResizing}
            />
          )}
          <aside
            aria-label="Notebook panel"
            ref={aside}
            aria-hidden={!isOpen}
            data-open={isOpen}
            className={[
              "min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden max-md:overflow-x-auto bg-card transition-opacity duration-300 ease-out max-md:border-t",
              isOpen ? "opacity-100" : "pointer-events-none opacity-0 max-md:hidden",
            ].join(" ")}
          >
            <div className="h-full min-w-[360px]">
              <NotebookPanel props={panel} callAction={callAction} />
            </div>
          </aside>
        </div>
      </div>
      <SettingsDialog
        open={isSettingsOpen}
        onOpenChange={setIsSettingsOpen}
        switches={settingsForModel(settings, canThink)}
        onChange={changeSetting}
        theme={theme}
        onThemeChange={changeTheme}
      />
      <Toasts />
    </div>
  );
}

function KaggleChatPrompts({ prompts }: { prompts: PromptView[] }): JSX.Element {
  const { send } = useConnection();
  const region = useRef<HTMLElement>(null);
  const nextOpen = prompts.find((prompt) => prompt.outcome === "open")?.askId;
  useEffect(() => {
    if (!nextOpen) return;
    const frame = window.requestAnimationFrame(() => region.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [nextOpen]);
  return (
    <section ref={region} aria-label="Kaggle download approvals" className="max-h-[40vh] space-y-2 overflow-y-auto px-4 py-2">
      {prompts.map((prompt) => (
        <PromptCard key={prompt.askId} prompt={prompt} onAnswer={(askId, choice) => send({ type: "ask.answer", ask_id: askId, choice })} />
      ))}
    </section>
  );
}
