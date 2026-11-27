// One answer: its work group, its questions, the answer text and Copy.
import { Answer, CopyButton } from "@/chat/components/Answer";
import { PromptCard } from "@/chat/components/PromptCard";
import { WorkGroup, type WorkStatus } from "@/chat/components/WorkGroup";
import type { AnswerView, PromptView } from "@/chat/store";
import mark from "@/assets/favicon.svg";

export interface AssistantMessageProps {
  answer: AnswerView;
  prompts?: PromptView[];
  onAnswer?: (askId: string, choiceId: string) => void;
}

function workStatus(answer: AnswerView, prompts: PromptView[]): WorkStatus {
  if (prompts.some((prompt) => prompt.outcome === "open")) return "waiting";
  if (answer.interruptionReason) return "interrupted";
  if (answer.isStreaming && !answer.group?.isDone) return "running";
  return "finished";
}

export function AssistantMessage({ answer, prompts = [], onAnswer = () => {} }: AssistantMessageProps): JSX.Element {
  const status = workStatus(answer, prompts);
  const showActivity = answer.group || status === "waiting" || (status === "running" && !answer.text);
  return (
    <div className="st-enter flex items-start gap-3">
      <img src={mark} alt="" className="mt-0.5 size-7 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {showActivity && <WorkGroup group={answer.group} status={status} />}
        {prompts.map((prompt) => (
          <PromptCard key={prompt.askId} prompt={prompt} onAnswer={onAnswer} />
        ))}
        {answer.text && <Answer text={answer.text} />}
        {answer.interruptionReason === "openrouter_relay_failed" && (
          <p role="status" className="text-sm text-muted-foreground">
            This reply was interrupted because OpenRouter couldn’t route the request. You can send your message again in a moment.
          </p>
        )}
        {answer.text && !answer.isStreaming && (
          <div className="-ml-2 flex items-center text-xs text-muted-foreground">
            <CopyButton text={answer.text} label="Copy answer" />
          </div>
        )}
      </div>
    </div>
  );
}
