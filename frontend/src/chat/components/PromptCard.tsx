// A Run / Skip style question with its buttons, then what was chosen.
import { useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { Answer } from "@/chat/components/Answer";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PromptView } from "@/chat/store";

export const TIMED_OUT = "No answer, skipped";

export function PromptCard({ prompt, onAnswer }: { prompt: PromptView; onAnswer: (askId: string, choiceId: string) => void }): JSX.Element {
  const [hasAnswered, setHasAnswered] = useState(false);
  const isOpen = prompt.outcome === "open";
  const chosenLabel = prompt.choices.find((c) => c.id === prompt.chosen)?.label;
  const answer = (choiceId: string) => {
    setHasAnswered(true);
    onAnswer(prompt.askId, choiceId);
  };
  return (
    <div className={cn("st-enter overflow-hidden rounded-xl border bg-card", isOpen && "border-warning/45")}>
      <div className={cn("flex items-center gap-2 bg-muted/40 px-4 py-2.5 text-sm font-semibold", isOpen && "bg-warning/10 text-warning")}>
        <ShieldQuestion className="size-4 shrink-0" aria-hidden />
        {prompt.kaggleRequestId ? "Confirm Kaggle download" : isOpen ? "Decision needed" : "Decision"}
      </div>
      <div className="space-y-3 px-4 py-3">
        <Answer text={prompt.markdown} />
        {isOpen ? (
          <div className="flex flex-wrap gap-2">
            {prompt.choices.map((choice, index) => (
              <Button key={choice.id} variant={index === 0 ? "default" : "outline"} disabled={hasAnswered} onClick={() => answer(choice.id)}>
                {choice.label}
              </Button>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{prompt.outcome === "timed-out" ? TIMED_OUT : `You chose ${chosenLabel ?? prompt.chosen}`}</p>
        )}
      </div>
    </div>
  );
}
