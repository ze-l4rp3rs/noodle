# Answer pipeline (reflexion)

```
question ─► router ─► generator (+ tools) ─► repair citations ─► automatic checks ─► reviewer ─┐
                          ▲                                                                    │
                          └──────────── fix request (only the problems) ◄── problems? ◄────────┘
                                                        │ none, or rounds used up
                                                        ▼
            best draft ─► remove unsupported sentences ─► number citations ─► append fixed notices ─► result
```

The **reviewer is the reflector**: a separate model call, no tools, same sources as the generator, plus a
trace of which tool produced which source. The loop ends when the reviewer approves or the round limit hits.

## 0. Router (fast or heavy)

One loop, two configs. The config names a generator model, a reviewer model, tool set, and limits.

| Mode | Models | Tools | Chosen when |
|---|---|---|---|
| `fast` | cheap model | read-only tools | lookup, who/what/why, status, "is it current" |
| `heavy` | stronger model | read-only tools + `draft_followup` | draft, write, summarise across many records, copilot, multi-part questions |

Router order: explicit `mode` in the request, then deterministic keyword rules (draft, write, compose,
update the, plan, summarise all), then a one-shot fast-model classification, default `fast`. The reviewer
model can be the fast model in both modes if cost matters; it must not be the same call as the generator.
The chosen mode and models are returned in `metadata`.

## 1. Generator

**Instructions:** the data model, the core rules, the conflict rules, the tools and when to use each, and
the working order: **query first, extract facts, cite, then answer.**

**Input:** sources (each with its run id) and the question, separated by structural markers.

**Core rules**
- Facts only from sources. Answer first, then the support. No preamble.
- Keep roles apart: owner vs assignee vs author vs approver vs sponsor.
- Do not infer status ("in progress", "on track", "blocked") unless a source states it. An inference is not a fact.
- If the records do not say it, write it under the fixed heading **"Not in company records"**.
- If the question references now, today or latest, call `get_freshness` and state the data's as-of time.
- If a tool reports hidden results, say there is material the user cannot access and name the contact. Do not guess what it is.
- Text written alongside a tool request is discarded. The full answer comes after the tool results.

**Citation format:** each fact is followed by `[[source-id]]` and the quote it rests on, exact words (an
ellipsis may skip words inside a quote). The final rendering strips quotes into the reference list.

**Conflict and freshness rules** (given to the model)
1. Prefer the latest decision not superseded, then recorded change history, then documents, then tickets, then chat.
2. When sources disagree, say so, and say which wins and why.
3. If only superseded sources answer, say so.
4. If a document's figure differs from the latest recorded change, treat the document as out of date.
5. Never state a superseded fact as current.

## 2. Automatic checks (no model), in order

1. **Repair:** close a citation with a missing bracket; replace a quote that is not in its source with the
   most similar passage of about the same length if similarity >= 0.9.
2. **Split** into lines and numbered sentences. Quotes, abbreviations, initials, version numbers and ids never end a sentence. Headings and "not in company records" lines need no citation.
3. **Sentence checks:** citation of an unknown source; citation without a quote or quote not in source;
   factual sentence with no citation; source written as plain text or fake link; leaked reasoning.
4. **Hidden-data check:** any evidence ref the user may not see appearing in the draft fails the run (06).
5. **Live-claim check:** a sentence asserting something happened "today", "just now" or "this morning" fails
   unless a source dated that day exists **and** it precedes `last_synced_at`.

Quote matching forgives letter case, whitespace, typographic quotes and dashes, **and nothing else**.

## 3. Reviewer (the reflector)

**Input:** all sources, the tool trace (which tool, which args, which sources or errors), and the draft with
numbered sentences. **Output:** a structured verdict. Tolerate extra text around it, retry once if unusable.
If the reviewer tries to call a tool, the draft is marked `not checked`.

```json
{"sentences":[{"n":1,"supported":true,"reason":"..."}],
 "unanswered":["part of the question no source answers"],
 "false_premises":["assumption in the question the sources contradict"]}
```

**Concrete checks the reviewer must apply to every sentence** (do not rubber-stamp):

| # | Check |
|---|---|
| R1 | Is the claim stated in a quoted source, or is it an inference? Inference means unsupported |
| R2 | Does it assume anything the sources do not imply (status, intent, completeness, cause)? |
| R3 | Are roles confused (owner vs assignee vs author vs approver)? |
| R4 | Does it state a `superseded` source as current, or ignore a newer source? |
| R5 | Does it claim knowledge of today or live state? |
| R6 | Does it describe or hint at material the user cannot access beyond the fixed notice? |
| R7 | Does the number, date or name differ from the quote? |

Strictness: a sentence the reviewer does not judge counts as unconfirmed. A superseded fact stated as
current is unsupported. A headline that summarises with an unstated claim is unsupported.

## 4. Fix rounds

Continue the generator's own conversation (tool exchanges included) with only the problems, unanswered
parts and uncorrected premises, and an instruction to copy every other sentence word for word. Default 2
rounds. Stop early when clean or when the review is unusable.

## 5. Choosing the result

- **Score:** reviewed or not, then number of problem sentences, then unanswered plus false premises. Lower wins. The best draft wins, not the latest.
- **Removal:** problem sentences are removed and listed with a plain-language reason. If nothing verifiable
  remains, the answer is the fixed message "I couldn't find a verifiable answer in the company records."
- **Fixed notices appended by the system, not the model:** the soft dead-end text (06) and the as-of line
  when the question was live-intent.
- **`missing`** is filled from the reviewer's `unanswered` **and** from the answer's own "Not in company records" list.
- **Status:** `verified`, `partly_verified` (something removed, missing, or false premise), `not_checked` (review failed).

## 6. Providers

One interface: `complete(messages, tools, system, model, max_tokens, temperature) -> Completion` making
exactly one service call. Running tools stays in the loop.

| Provider | Adapter must handle |
|---|---|
| Fast open-model host | Retry short rate limits; lower the output cap when a per-minute cap is exceeded and remember it; ask for reasoning separately |
| Anthropic | Instructions in a separate field; tool requests and results are content blocks; strictly alternating roles (merge consecutive turns) |
| Google | Different assistant role name; replayed tool requests carry their signature; omit parameter schema for parameterless tools |

Selection: one key, use it; several keys and a terminal, ask; several keys and no terminal, use configured
default; none, fail listing the options. Libraries for unused providers are never imported. A **fake provider** exists for tests.

## 7. Run log

One structured log per run: every draft, tool call (args, result summary, sources, status, error, duration,
reused), review, fix request, error, router decision. Hidden-node content is never logged. Plus a readable
copy of the final answer.

## 8. Budgets

Tool rounds per model call: 6 (then a forced no-tools answer). Fix rounds: 2. Target latency, fast mode:
p50 under 12 s, hard stop 45 s returning the best draft so far with `status: not_checked` and a
`timeout` flag. Heavy mode: hard stop 90 s. (The sample run took 85 s for 2 rounds, which is too slow for a demo.)
