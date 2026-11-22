// Loads the connected account's own Kaggle datasets each time the window opens, through the panel action.
import { useCallback, useEffect, useRef, useState } from "react";
import type { CallAction } from "@/panel/types";
import { answerFromServer, LIST_MY_DATASETS_ACTION, UNREACHABLE_ANSWER, type MyDatasetsAnswer } from "@/chat/lib/kaggleDatasets";

export type MyDatasetsView = { phase: "loading" } | { phase: "answered"; answer: MyDatasetsAnswer };

export interface MyKaggleDatasets {
  view: MyDatasetsView;
  /** Ask Kaggle again (Try again) */
  retry: () => void;
}

export interface KaggleListingContext {
  /** Invalidates list results when the connected account changes or the socket reconnects. */
  connectionGeneration: number;
  isOnline: boolean;
}

/**
 * The window's list while it is open. `hasSignInFinished` turns true when a Kaggle sign-in ends connected,
 * so a Connect from the window shows the list as soon as Kaggle says yes.
 */
export function useMyKaggleDatasets(
  callAction: CallAction, hasSignInFinished: boolean, context?: KaggleListingContext,
): MyKaggleDatasets {
  const generation = context?.connectionGeneration ?? 0;
  const isOnline = context?.isOnline ?? true;
  const [loaded, setLoaded] = useState<{ generation: number; view: MyDatasetsView } | null>(null);
  const view: MyDatasetsView = isOnline && loaded?.generation === generation ? loaded.view : { phase: "loading" };
  // only the newest request may fill the window; closing the window outdates every request
  const newestRequest = useRef(0);

  const load = useCallback(() => {
    newestRequest.current += 1;
    const request = newestRequest.current;
    const showIfNewest = (answer: MyDatasetsAnswer) => {
      if (request === newestRequest.current) setLoaded({ generation, view: { phase: "answered", answer } });
    };
    setLoaded(null);
    if (!isOnline) return;
    callAction({ name: LIST_MY_DATASETS_ACTION, payload: {} }).then(
      (result) => showIfNewest(answerFromServer(result)),
      () => showIfNewest(UNREACHABLE_ANSWER),
    );
  }, [callAction, generation, isOnline]);

  useEffect(() => {
    load();
    return () => {
      newestRequest.current += 1;
    };
  }, [load]);
  useLoadAgainWhenConnected(hasSignInFinished, load);

  return { view, retry: load };
}

function useLoadAgainWhenConnected(hasSignInFinished: boolean, load: () => void): void {
  const wasFinished = useRef(hasSignInFinished);
  useEffect(() => {
    const hasJustConnected = hasSignInFinished && !wasFinished.current;
    wasFinished.current = hasSignInFinished;
    if (hasJustConnected) load();
  }, [hasSignInFinished, load]);
}
