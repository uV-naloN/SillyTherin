// The chat socket: one per tab. It keeps the chat id only in memory, so a dropped link
// resumes the same chat while a page reload starts a new one.
import type { PageEvent, ServerEvent } from "@/chat/events";
import { newer } from "@/panel/lib/newer";
import type { CallAction, NotebookProps } from "@/panel/types";

export const SOCKET_PATH = "/ws/chat";
export const RECONNECT_FIRST_DELAY_MS = 500; // doubles on each failed try
export const RECONNECT_MAX_DELAY_MS = 10_000;
const SOCKET_OPEN = 1;
const OFFLINE = { error: { code: "OFFLINE", message: "Lost the link to the app. Try again in a moment." } };
const NOT_SENT = "Not connected to the app yet. Try again in a moment.";

export type ConnectionStatus = "connecting" | "open" | "reconnecting";

export interface ConnectionDeps {
  /** Where the page came from, e.g. "http://127.0.0.1:8000" */
  pageOrigin: string;
  /** Opens a socket; tests pass a fake */
  openSocket: (url: string) => WebSocket;
  onEvent: (event: ServerEvent) => void;
  onStatus: (status: ConnectionStatus) => void;
}

interface PendingAction {
  name: string;
  settle: (result: unknown) => void;
}

const isProjectAction = (name: string) => name === "nb_switch_project" || name === "nb_create_project";

function projectSnapshot(result: unknown): result is NotebookProps {
  if (!result || typeof result !== "object" || !("project" in result) || !("datasets_rev" in result)) return false;
  const props = result as NotebookProps;
  return !props.error && typeof props.project?.id === "string";
}

export class ChatConnection {
  private socket: WebSocket | null = null;
  private chatId: string | null = null;
  private failedTries = 0;
  private requestCount = 0;
  private readonly waiting = new Map<string, PendingAction>();
  private readonly switchPushes = new Map<string, NotebookProps>();
  private latestProjectRequest: string | null = null;

  constructor(private readonly deps: ConnectionDeps) {}

  /** Open the socket for a new chat */
  start(): void {
    this.open();
  }

  send(event: PageEvent): boolean {
    if (!this.isOpen()) {
      this.deps.onEvent({ type: "toast", kind: "warning", text: NOT_SENT });
      return false;
    }
    this.socket!.send(JSON.stringify(event));
    return true;
  }

  /** The panel's callAction: a panel.action answered by the panel.result with the same request id */
  readonly callAction: CallAction = ({ name, payload }) =>
    new Promise((resolve) => {
      if (!this.isOpen()) {
        resolve(OFFLINE);
        return;
      }
      this.requestCount += 1;
      const requestId = `r${this.requestCount}`;
      this.waiting.set(requestId, { name, settle: resolve });
      if (isProjectAction(name)) this.latestProjectRequest = requestId;
      this.socket!.send(JSON.stringify({ type: "panel.action", request_id: requestId, name, payload }));
    });

  /** The socket URL for this page, with the chat id when resuming */
  socketUrl(chatId: string | null): string {
    const url = new URL(SOCKET_PATH, this.deps.pageOrigin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    if (chatId) url.searchParams.set("chat_id", chatId);
    return url.toString();
  }

  private isOpen(): boolean {
    return this.socket !== null && this.socket.readyState === SOCKET_OPEN;
  }

  private open(): void {
    const socket = this.deps.openSocket(this.socketUrl(this.chatId));
    this.socket = socket;
    this.deps.onStatus(this.chatId ? "reconnecting" : "connecting");
    socket.onmessage = (message: MessageEvent) => this.receive(socket, JSON.parse(String(message.data)) as ServerEvent);
    socket.onclose = () => this.dropped(socket);
  }

  private receive(socket: WebSocket, event: ServerEvent): void {
    if (socket !== this.socket) return;
    if (event.type === "panel.result") {
      this.finishPanelAction(event);
      return;
    }
    if (event.type === "panel.props" && this.latestProjectRequest !== null) {
      const project = event.props.project.id;
      const previous = this.switchPushes.get(project);
      this.switchPushes.set(project, newer(previous ?? null, event.props));
    }
    if (event.type === "session.ready") {
      this.chatId = event.chat_id;
      this.failedTries = 0;
      this.deps.onStatus("open");
    }
    this.deps.onEvent(event);
  }

  private finishPanelAction(event: Extract<ServerEvent, { type: "panel.result" }>): void {
    const pending = this.waiting.get(event.request_id);
    this.waiting.delete(event.request_id);
    if (this.latestProjectRequest === event.request_id) {
      if (projectSnapshot(event.result)) {
        const incoming = this.switchPushes.get(event.result.project.id);
        const props = incoming ? newer(event.result, incoming) : event.result;
        this.deps.onEvent({ type: "panel.project_changed", props });
      }
      this.latestProjectRequest = null;
      this.switchPushes.clear();
    }
    if (pending?.name === "kg_download_dataset") this.deps.onEvent(event);
    pending?.settle(event.result);
  }

  private dropped(socket: WebSocket): void {
    if (socket !== this.socket) return;
    this.socket = null;
    for (const pending of this.waiting.values()) pending.settle(OFFLINE);
    this.waiting.clear();
    this.latestProjectRequest = null;
    this.switchPushes.clear();
    this.deps.onStatus("reconnecting");
    const delay = Math.min(RECONNECT_FIRST_DELAY_MS * 2 ** this.failedTries, RECONNECT_MAX_DELAY_MS);
    this.failedTries += 1;
    setTimeout(() => this.open(), delay);
  }
}
