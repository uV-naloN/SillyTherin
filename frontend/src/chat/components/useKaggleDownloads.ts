import { useEffect, useMemo, useRef, useState } from "react";
import type { CallAction, ErrorProps, NotebookProps } from "@/panel/types";

export const KAGGLE_DOWNLOAD_ACTION = "kg_download_dataset";

export interface KaggleDownloadPayload {
  /** Guard against a project change while the picker was open. */
  project_id: string;
  /** Canonical owner/slug from the current listing. */
  ref: string;
}

export type KaggleDownloadResult =
  | { state: "accepted"; dataset_id: string }
  | { state: "skipped" }
  | { state: "error"; error: ErrorProps };

export type KaggleDownloadRowState = "available" | "checking" | "downloading" | "ready";

export interface KaggleDownloadRow {
  state: KaggleDownloadRowState;
  error?: string;
}

export interface KaggleDownloads {
  rowFor: (ref: string) => KaggleDownloadRow;
  start: (ref: string) => Promise<void>;
  projectName: string | null;
}

export interface KaggleDownloadContext {
  callAction: CallAction;
  panel: NotebookProps | null;
  generation: number;
  isOnline: boolean;
}

interface RowRequest {
  context: string;
  isChecking: boolean;
  error?: string;
}

function actionMessage(result: unknown): string | undefined {
  if (result && typeof result === "object" && "error" in result) {
    const error = result.error as Partial<ErrorProps> | null;
    if (typeof error?.message === "string") return error.message;
  }
  return undefined;
}

export function useKaggleDownloads({ callAction, panel, generation, isOnline }: KaggleDownloadContext): KaggleDownloads {
  const projectId = panel?.project.id;
  const context = isOnline ? `${projectId ?? ""}:${generation}` : "";
  const contextNow = useRef(context);
  contextNow.current = context;
  const pending = useRef(new Set<string>());
  const [requests, setRequests] = useState<Record<string, RowRequest>>({});
  useEffect(() => {
    setRequests({});
  }, [context]);
  const cards = useMemo(() => new Map(panel?.datasets.filter((card) => card.kaggle_ref)
    .map((card) => [card.kaggle_ref?.toLowerCase(), card])), [panel?.datasets]);

  function rowFor(ref: string): KaggleDownloadRow {
    const key = ref.toLowerCase();
    const request = requests[key]?.context === context ? requests[key] : undefined;
    const card = cards.get(key);
    if (card?.status === "ready") return { state: "ready" };
    if (card?.status === "downloading" || card?.status === "queued") return { state: "downloading" };
    if (request?.isChecking) return { state: "checking" };
    return { state: "available", error: request?.error ?? card?.error ?? undefined };
  }

  async function start(ref: string): Promise<void> {
    if (!isOnline || !projectId || rowFor(ref).state !== "available") return;
    const key = ref.toLowerCase();
    const pendingKey = `${context}/${key}`;
    if (pending.current.has(pendingKey)) return;
    pending.current.add(pendingKey);
    setRequests((current) => ({ ...current, [key]: { context, isChecking: true } }));
    let error: string | undefined;
    try {
      const payload: KaggleDownloadPayload = { project_id: projectId, ref };
      error = actionMessage(await callAction({ name: KAGGLE_DOWNLOAD_ACTION, payload: { ...payload } }));
    } catch {
      error = "Could not start the download. Try again.";
    } finally {
      pending.current.delete(pendingKey);
    }
    if (contextNow.current === context) {
      setRequests((current) => ({ ...current, [key]: { context, error, isChecking: false } }));
    }
  }

  return { rowFor, start, projectName: isOnline ? panel?.project.name ?? null : null };
}
