# NOTES: knowledge system for the credit union snapshots

## What this is

`solution.py` exposes `build()`. The object it returns has `ingest(snapshot)`, `answer(question, as_of=None)`
and `conflicts()`, as defined in `harness/contract.py`. The package `kb/` holds the code.

Required API keys: **none**. Paid API use: **none**. The system uses only the Python 3.11 standard library.
It makes no network call. It uses only the supplied snapshots as evidence.

## Architecture

Agents are plain Python components. A language model is not needed for the structure in these pages
(Markdown tables, headings, lists, dated press releases). Deterministic code gives repeatable answers, which
the consistency score rewards, and it keeps ingestion at about 25 seconds for all five snapshots.

| Agent (file) | Task |
|---|---|
| Coordinator (`kb/agents/coordinator.py`) | Takes snapshots oldest to newest. Parses each page once. Routes each block to a specialist. Page tasks are independent (optional thread pool). Merges all results through one write path (`Store.add_page`, `Store.add_snapshot`). Checks that every page of the snapshot is stored before the next snapshot starts. |
| Rates agent (`kb/agents/rates.py`) | Reads rate tables and rate sentences. Keeps interest rate, dividend rate, APR and APY apart. Keeps tier, term, loan amount, vehicle years and minimum deposits as conditions. Reads the `effective as of` date after each table. |
| Fees and products agent (`kb/agents/fees_products.py`) | Reads fee statements, non-rate tables, product text, eligibility text and statistics (for example `22 branch office locations`). |
| Branch agent (`kb/agents/branches.py`) | Reads the locator entries on the contact page (address, branch or ATM, services) and hours and phone text. The live `Closed` widget text is dropped because it is not a fact. |
| History and conflict agent (`kb/agents/history.py`) | Compares facts with the same meaning and conditions. Builds `conflicts()`. Chooses a winner when a question meets a contradiction. |
| Evidence checker (`kb/agents/checker.py`) | Marks a fact `verified` only when its value, entity and every condition appear in the page text. Unverified facts are not used for conflicts. A resolution is accepted only if the winning value appears in its own source quote. |

Shared records are in `kb/records.py`. A `Fact` has entity, attribute, value, unit, conditions, URL, quote,
effective date, expiry, status and kind. A `Chunk` is one searchable passage (table row, paragraph, list item
or location) with its facts. Evidence (the quote, URL and snapshot) is kept apart from interpretation.

Storage (`kb/store.py`):
* Each unique page text is stored once, keyed by URL and content hash. Unchanged pages are not parsed again.
* Each snapshot keeps its own page-membership map. A page that disappears stays in older snapshots.
* A BM25 inverted index covers all chunks. A query is limited to the pages of one snapshot.

## How current and historical facts are selected

1. `as_of=None` means the latest snapshot (28 September 2026). With `as_of`, the system uses the latest snapshot
   dated on or before that day. A month-year phrase in the question (for example `as of May 2026`) is turned into
   the end of that month when no `as_of` is given. Later snapshots are never used for an earlier date.
2. A date before the first snapshot (12 May 2026) returns `I don't know`, with the first available date.
3. Inside one snapshot, candidates are ranked by BM25, then adjusted by:
   * the conditions in the question (term, loan amount, vehicle year, APY or APR) and rate-table rows;
   * source purpose: rate sheet 1.0, product and contact pages 0.9, other site pages 0.7, promotion pages 0.5,
     news 0.45, blog 0.35, calculators 0.3;
   * expired or ended offers (text such as `offer ended`, or an expiry date before the snapshot date);
   * business versus personal wording, and introductory-rate wording;
   * newer news pages (page date) and newer `effective as of` dates.
4. Table rows are shown with their tier or term. If the question gives no tier, the whole table is shown, so
   the member sees every tier. If it gives a tier, only the matching rows are kept (hard filter).
5. A row that repeats an older copy of a rate table on another page is replaced by the same row with a clearly
   newer effective date (and a similar or higher source rank).
6. A page that was removed from the website is used only when no current page answers. The answer then says
   that the page was last seen on a stated date and may be out of date.
7. Change questions (`did the rate change`, `how has ... changed`) are answered for each snapshot. The answer
   reports the earlier and the current value, or says that the snapshots show no change.
8. Refusal: the answer is `confident=False`, with no numbers, when the evidence is too weak. The checks are:
   * word coverage of the question (synonyms such as `CD` = certificate and word forms such as `ranked` = `rank`
     count; shape words such as `top` or `total` do not);
   * named things (for example `Mullan`, including a rare capitalized first word) must be in the evidence;
   * rate questions need a rate statement (`rate 7.25%`, `7.25% APR`), not any percent (`3% down payment`), and
     the product word in front of `loan`, `card` or `account` (`construction loan`, `auto loan`) must be in it;
   * hours questions need real schedule lines, and a named branch must be the page that holds them;
   * fee questions need a dollar value, phone questions a phone number, and special numbers (for example `7/1`)
     must appear.
9. Count questions (`how many branches`) count only statements about the credit union, never national figures,
   and the newest dated statement wins.
10. Link questions (`which page shows`, `apply online`) prefer an information page over an application link,
    unless the member wants to apply.
11. Value-exists questions (`do any savings tiers pay 0.35% APY?`) read every matching product table on the most
    authoritative rate page. The answer gives the top tier of the first-listed (main) product and every tier
    that has the asked value, or says that no tier has it. The nearest row with that number is not an answer.
12. An answer that refers to an area without naming it (`within our charter area`) gets the sentence on the
    same page that names the place (`You can find us in Iowa, Illinois and Wisconsin.`).
13. Sources: other pages of the same snapshot that state the same routing number, phone number or
    days-and-hours span (in other words) are cited as more evidence, up to eight sources.

## How conflicts are handled

Three cases are kept apart:
* **Change**: one fact has a new value in a later snapshot. This is history, not a conflict.
  The history agent keeps the value timeline for each fact.
* **Contradiction**: two sources in the same snapshot give different values for the same entity, attribute and
  conditions. These are listed by `conflicts()` (for the latest snapshot).
* **Different conditions**: tiers, terms, products and branches differ. These are not compared, because the
  conditions are part of the fact key.

The fact key pools the words of the entity and the conditions. Pages split these differently, so pooling lets
a stale table on one page meet the current table on another page.

The winner is not chosen by majority vote. The rank is (source purpose after penalty for ended or expired
offers, effective date or page date). A resolution is set only if the top source is strictly better than the
best source with a different value, and if its value appears in its quote. Otherwise `resolution` is `None`
and the reason says the case is unresolved.

Statistics (staff, branches, counties, members) become facts only when they describe the whole organisation
(`It has over 600 employees and 22 branch office locations`) and fall in a plausible range. A sentence such as
`294 employees used Volunteer Time Off` describes a group, so it is not compared with the staff total.

## Guardrails and security

* No network calls, no API keys, no `eval`/`exec`/`pickle`/`subprocess` in the submission. Standard library only.
* Input: the question is cut to 1,000 characters and control characters (including right-to-left marks) are
  removed. Amounts, years and terms in a question are bounded, so hostile input cannot overflow or slow the
  answer (every test input answers in well under 2 seconds). A question that names nothing (`rate?`,
  `' OR 1=1 --`) gets a request to name the product. A bad `as_of` value gets a clear date-format message.
* Ingestion: a damaged page is logged and skipped, a page that is not an http(s) URL is ignored, a page larger
  than 2,000,000 characters is cut, the same snapshot twice is ignored, and a snapshot that arrives out of
  date order is put in order and the history is rebuilt. A failed snapshot does not stop the others.
* `answer()` never raises: an internal error returns an honest `I don't know`. Ingestion and `conflicts()` are
  serialized with a lock.
* The UI server (below) binds to 127.0.0.1, serves only a fixed list of files, checks the Host and Origin
  headers (blocks DNS rebinding and cross-site posts), limits request size and rate, accepts only JSON, refuses
  other methods, sends strict security headers (Content-Security-Policy, no framing, no sniffing, no referrer),
  never logs question text, and the page inserts all server text with `textContent` only.
* Tests: `python -m unittest discover -s submission/tests -v` (contract, date rules, refusals, hostile input,
  snapshot order, damaged pages, follow-up memory, saved-copy round trip, value-exists questions,
  same-statement sources). They use only the artificial self-test data.

## Web UI (optional, not used by the scorer)

```
python submission/app/server.py            # then open http://localhost:8765
python submission/app/server.py --no-cache # always rebuild; do not read or write the saved copy
```

The UI uses the same `build()` object as the scorer. Tabs:
* **Chat**: a conversation thread. Each answer has its own **Sources** box (pages, the date of each copy and the
  saved quote), **Why this value** (the reason for a chosen value) and **Other values on the website** (the
  contradictions that touch this answer, with the chosen value marked). There is no separate conflicts page.
* **As of** menu under the message box: `Current (September 28, 2026)`, the earlier snapshot dates, or
  `Another date…` (month, day and year menus, preset to today). The note under it says which copy is used.
  All dates are shown in the full form (`September 28, 2026`), also inside answers.
* **Features**: what the assistant does for a member, example questions by topic (one click asks them in the
  chat) and how to read an answer. It does not show the internal design.
* **Snapshots**: a timeline of the copies to scale, with today's date and the time after the newest copy shaded,
  and bars of the pages in each copy (new or changed, and the same as before). Then the start time.
* **FAQ**.

It works in light and dark mode and on a phone.

Load time. The first start reads all pages (about 20 to 25 seconds in the UI). The server then saves what the
agents learned to `submission/app/.cache/knowledge.json.gz` (git-ignored; gzip JSON, so loading it cannot run
code, unlike pickle). Later starts load it in about 6 seconds. The file has a fingerprint of every snapshot page
and of the `kb/` source code; a changed snapshot or a code change rebuilds it. A damaged file is refused and
nothing in memory changes. The scorer does not use this cache: `build()` + `ingest()` behave as before.

## Memory

| Memory type | In this system |
|---|---|
| In-context (working) | `kb/working_memory.py`: the chat keeps the last question. A follow-up that changes a condition (`What about 72 months?`, `and in May 2026?`) replaces that condition. A follow-up that asks for a new attribute (`What are its hours?`) keeps the subject. The UI shows the joined question as `Read as`. `New chat` clears it. |
| Semantic | The fact store: `Fact` records with entity, attribute, value, conditions, quote and source. |
| Episodic | The snapshot timeline with per-snapshot page membership; `as_of` answers and change questions. |
| Procedural | The agent rules: source authority, effective dates, refusal checks, table reading. |
| External (retrieval) | The BM25 index over all chunks, and the saved copy on disk. |
| Parametric | Not used, on purpose. No language model writes the facts, so no value can be invented. |
| Prospective | Offer end dates are stored; after its end date an offer loses to the standard rate. |

## Known limits

* Value-exists questions are handled for rate tables only. Other negation or counting across rows is answered
  with the nearest evidence, not with a reasoned `no`.
* A public item with no `must_include` values (`Get me the rate for the certificate of accounts.`) scores 0
  unless the scorer's optional LLM judge is on; the answer reads back every certificate rate, as expected.
* Some answers are in unstructured pages. Retrieval picks passages; it does not rewrite them.
* A question about something not on the website (for example a 7/1 ARM rate) returns `I don't know`.
* The `Conflicts` score depends on the hidden reference. The public run does not measure it.
* Follow-up memory uses rules, not a language model. A long new question that starts with `and` is joined to
  the last question; `Read as` shows this, and `New chat` clears it.
* Page tasks are independent and can run on a thread pool (`Coordinator(workers=4)`), but the work is pure
  Python, so threads were slower (52.6 s against 35.0 s). The default is one worker.

## How to run and measured results

```
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
python harness/run_eval.py --submission submission --snapshots harness/selftest/snapshots \
       --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json
```

Measured on this machine with Python 3.11.9:

| Check | Result |
|---|---|
| Public eval accuracy | 0.97 (starter: 0.09) |
| Same 40 questions, reworded by us | 0.93 |
| 20 further rewordings, written after the fixes (held out) | 1.0 |
| Paraphrase and repeat consistency | 1.0 |
| Answer time p95 | about 0.1 s |
| Ingestion of all five snapshots | 18 to 55 s (limit: 30 minutes) |
| UI start: first / from the saved copy | 23 s / 6 s |
| Self-test (with conflict reference) | 1.0 |
| Unit tests | 25 of 25 pass |
| `conflicts()` on the real data | 37 contradictions, all resolved |

The reworded sets are test files outside the submission. They check generalization; they are not a lookup
table, and no answer is stored anywhere in the code.

The snapshot data is not part of the repository and must not be committed.
