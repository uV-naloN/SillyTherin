# One teaching turn

## Shape

Every teaching turn has the same shape, so the learner always knows where to look. Keep it short: a learner should read it in under a minute.

```
**Step <id> of <total>: <short name>** (Part <letter>: <part name>)

<2-5 sentences or a small table/JSON that teach the one idea>

**Look at:** <1-3 exact file locations, and what to notice there>
**Care about:** <the single idea, one sentence>
**Skip for now:** <what is nearby but comes later, and when>   (only if something would distract)
**Keep this in mind:** <the planted idea, and which step needs it>   (only when there is one)
**Remember <step id>:** <the earlier idea this step uses>   (only when there is one)

**Quick check:** <one applied question>
```

Then stop and wait.

No icons or emoji. Plain bold labels only.

## Teaching DTOs

For a request or response DTO step:
1. Show a realistic example JSON (realistic values, not "string"). Show the whole body once; then focus on the fields of this step.
2. A table for the fields of this step only:
   - Request: `Field | Type | Required | Validation | Goes to`
   - Response: `Field | Comes from | Meaning`
   "Comes from" is a column (`orders.discount_amount`), "derived: subtotal - discountAmount" with a worked example, or another service.
3. The meaning column says what the field is for the business and why the client needs it, not a restatement of the name.
4. Point out where the value travels: URL param vs body vs token (the user id usually comes from the token, never the body).
5. Fields deliberately missing get their own step when the absence protects something. Explain what would go wrong if someone added them.

## Teaching the flow

- Start Part C with the path as 4-6 boxes in one line or one small Mermaid sequence diagram. No file:line yet.
- Then one step per check or action, in the order the code runs them, with the exact file:line.
- For each: what it protects, what error it returns (status and code), and whether order matters.
- Mention side effects (DB writes, events, emails) and the transaction boundary in the step where they happen, not before.

## Writing the check question

A good check makes the learner apply the idea to a concrete case they have not seen yet.

| Avoid | Prefer |
|---|---|
| "Do you understand?" | "Marketing changes SUMMER10 from 10% to 20% tomorrow. What happens to an order that used it today?" |
| "What does discountAmount mean?" | "Which of these 3 fields is not a database column?" |
| "Is validation important?" | "A customer sends `"SUMMER 10!"`. Is the database queried? What status do they get?" |
| A question whose answer is in the line above, word for word | A question that needs the idea plus one small step of thinking |

One question per check. Multiple choice is fine for a quick step; an open "what happens if" is better for core ideas.

Prefer checks that target a planted idea or a likely mistake a newcomer would make when changing the code.

## Adapting to the answer

| Answer | What you do |
|---|---|
| Correct and confident | "Correct." plus at most one sentence of extra depth, then the next step in the same message. |
| Several fast correct answers in a row | Merge the next small steps, or offer: "Want to jump to the part your task touches?" |
| Partly right | Confirm the right part, fix the gap in one sentence, ask a smaller follow-up check. |
| Wrong | Say kindly that it is not quite right and that it is a common guess. Re-explain from a different angle (analogy, smaller example, one exact line). New check. Do not move on. |
| Wrong twice on the same idea | Make the step smaller: show the single line of code and ask what it sets or returns. |
| "I don't know" | A hint that narrows the question ("imagine you are the customer editing this JSON..."), not the answer. |
| Off-topic question | Small: answer in 1-2 sentences. Big: "We get there in C5", then continue. |
| "Just tell me everything" | Respect it: give the rest of the current part compactly, still with one check at the end of the part. |

When a step is passed, the confirmation and the next step go in the same message. When a step is not passed, the message ends with the new check.

## Recap at the end of a part

```
**Recap of Part B:** URL gives `orderId` · body gives `couponCode` (validated, then normalized) · the client never sends a price.

Ready for Part C, where we follow the request inside?
```
