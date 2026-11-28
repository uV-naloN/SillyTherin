# Building the hidden learning map

The learning map is your plan for the lesson. Build it after exploring the code and before the first teaching turn. The learner never sees the full table; they only see one step at a time and a short outline of the parts.

## 1. Set the goal

Write one line: what the learner must be able to do at the end.
- Learner has a task: "can add a max-uses rule to coupons without breaking pricing or security."
- Learner just wants to understand: "can explain the flow to a teammate and predict what happens for a given request."

The goal decides what is in the map and what is held back.

## 2. Find the core idea

Find the one or two ideas that everything else depends on. Examples: "the discount is stored on the order, not recalculated", "a session is owned by one user and every query filters by owner", "events are processed at least once, so handlers must be idempotent".

The core idea goes in Part A, before any code detail, because later steps keep referring to it.

## 3. Use the four parts

Always order the steps along the path a request travels:

| Part | Covers |
|---|---|
| A. The big picture | What it does for the user, who triggers it, why it exists, the core idea |
| B. What goes in | Entry point (method, URL or trigger, auth), path/query params, request DTO field by field, validation, normalization, fields deliberately missing |
| C. What happens inside | The path as a few boxes (one small diagram is fine here), then each check in order, the calculation, the saving (transaction, side effects) |
| D. What comes out, and changing it | Response DTO field by field (column or derived, with a worked example), fields deliberately hidden, error responses, then the learner's task or the main gotchas |

For a non-HTTP feature (job, consumer, webhook), Part B is the trigger and its payload, Part D is what it writes or emits.

## 4. Split into small steps

One idea per step. Rules of thumb:
- A DTO with more than 3 fields is split across steps (group fields by meaning: ids, money, nested object, metadata).
- "What is missing from the request/response" is its own step when the absence protects something (security, pricing).
- Each check in the flow that protects something is its own step (ownership, status, validation).
- A step that would need more than one code location is two steps.

Typical size: 8-15 steps for one main flow.

## 5. Fill each row

| Field | What to write |
|---|---|
| Step | Id and short name, e.g. `B2 Request DTO: couponCode` |
| Look at | The 1-3 exact places to open (`ApplyCouponRequest.java`, `Order.java:88`) |
| Care about | The single idea of this step |
| Ignore for now | The real things nearby that would distract, and where they come later |
| Carry forward | The idea to plant and the later step(s) that use it, e.g. `"order remembers its price" -> B3, D1, D4` |
| Check | One applied question with a concrete input, and the expected answer |

## 6. Decide what is held back

List the topics you will not teach unless the goal needs them or the learner asks (for example: audit logging, rate limiting internals, optimistic locking). For each, note the trigger that would bring it in: "bring in optimistic lock if the learner's task touches saving".

## 7. Keep the map alive

Update the map after every answer:
- Learner already knows a concept: mark the step as skipped or merge it.
- Learner struggles: insert a smaller step before the next one.
- Learner reveals a task: move held-back topics that the task touches into Part D.

## Example map (apply coupon to an order)

Goal: learner can add a "max uses per coupon" rule safely. Level: knows Spring, new to this codebase.

| Step | Care about | Ignore for now | Carry forward -> used in |
|---|---|---|---|
| A1 What it does | A code lowers the price of an unpaid order | All code | - |
| A2 Core idea | The discount is stored on the order | How it is calculated | "order remembers its price" -> B3, D1, D4 |
| B1 Endpoint | `PUT /orders/{orderId}/coupon`; `orderId` is in the URL | Auth details | "`orderId` from URL" -> C2 |
| B2 Request DTO: couponCode | Type, validation, 400 before DB, trim + uppercase | Response DTO | "input is normalized" -> C4 |
| B3 Missing from request | No discount or total field, and why | - | uses A2 |
| C1 The path | Filter -> Controller -> Service -> Validator -> DB | File:line | "cheap checks first" -> D3 |
| C2 Ownership | Loaded by id AND owner | - | "not yours = 404" -> D3 |
| C3 Status | Only PENDING can change | - | "paid price never changes" -> D4 |
| C4 Coupon validation | Active, not expired, minimum met | Clock details | "your rule goes here" -> D4 |
| C5 Calculate and save | 10% x 120 = 12.00, capped, one transaction | Audit log | "optimistic lock" -> D4 |
| D1 Response money fields | subtotal, discountAmount (stored), total (derived) | appliedCoupon | uses A2 |
| D2 Response appliedCoupon + hidden fields | 4 nested fields; no minOrderAmount, no internal ids | - | - |
| D3 Errors | Same 404, one COUPON_INVALID, on purpose | - | uses C1, C2 |
| D4 The learner's task | Where max-uses goes; traps: Clock, frozen discount, concurrent saves | - | uses A2, C3, C4, C5 |

Held back: audit log, in-memory rate limit (bring in only if asked or if the task touches them).
