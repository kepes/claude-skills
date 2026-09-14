---
name: session-cost-report
description: Per-session token, cost & time summary for a Claude Code project. Reads the project's recent session logs and reports, per session, how many tokens it used and what it would cost on Sonnet 5 / Opus 5 / Fable 5.1 — marking the model the session actually ran on — plus the real mixed cost (main model + subagents), the wall-clock span (how long the session ran, first→last event), and an estimated active working time (how long the LLM actually worked, idle pauses removed). Use when the user wants a token usage report, session cost breakdown, model cost comparison, session duration / how long a session took / how long Claude worked, or asks "how much did this session cost" / "how many tokens did I use" / "how long did this session run" / "what would this have cost on Sonnet/Fable". Defaults to the current project; supports "all projects" (összes projekt) with a project column, sorting by token count or by cost (rendezve token szám / költség szerint), and a configurable session count ("mutasd mindet" / "30-at"). Defaults to today but takes any time window ("elmúlt két nap", "elmúlt két óra", "ma három óra után", "tegnap"). Triggers (HU): "token összesítő", "token riport", "session költség", "mennyibe került a session", "mennyi tokent használtam", "költség összevetés", "mennyibe került volna Sonneten/Fable-ön", "token report", "összes projekt token", "rendezve token használat", "melyik projekt vitte a legtöbb tokent", "meddig futott a session", "mennyi ideig tartott", "mennyit dolgozott rajta a Claude", "session időtartam". Triggers (EN): "token usage report", "session cost", "how much did this cost", "cost per session", "token summary", "compare model cost", "token usage across all projects", "sort sessions by token usage", "how long did the session run", "session duration", "how long did Claude work on this". Triggered with /session-cost-report.
---

# Session token & cost report

Summarizes **today's** session logs of a Claude Code project (or of every project) as a
single table, one row per session (plus a TOTAL row). Columns:

| Column | Meaning |
|---|---|
| `PROJECT` | **Only present when more than one project is shown** — the project's short name. The first column in multi-project output. Hidden for single-project output. |
| `NAME` | Session name (AI-generated title) + short session id in parentheses |
| `MODEL` | Main (main-loop) model, with the subagent models in parentheses, e.g. `Sonnet (Opus)`. No parentheses if the session ran no subagents. |
| `MSGS` | Assistant message count — API responses, not log records (see the deduplication note under *How it works*) |
| `AGENTS` | Number of **subagent runs** that started inside the window — one per separate agent transcript (`<project>/<session-id>/**/agent-*.jsonl`), plus one per inline sidechain chain root in the main session file. This is a run count, not a message count: a session that fans out 200 agents shows `200`, whatever each agent wrote. `0` means the session did all work in its main loop. |
| `WALL` | **Wall-clock span** of the session — first in-window event to last. The real elapsed time the session was open, idle pauses included (e.g. a session left open all afternoon shows hours here). |
| `ACTIVE` | **Estimated working time** — the wall-clock span with idle stretches removed (gaps longer than the `--idle-gap` threshold, default 5m, are dropped as "user away"). The closest honest proxy for *how long the LLM actually worked*. It is an estimate: the logs record only one timestamp per event, no true per-request duration, so `ACTIVE` is derived from the event timeline, not measured. |
| `IN` / `OUT` | Uncached input / output tokens |
| `CACHE-R` / `CACHE-W` | Cache read / write tokens (input-side only) |
| `HIT` | Cache read as % of input (cache efficiency) |
| `SONNET` / `OPUS` / `FABLE` | Est. cost if the **whole** session ran on the current model of that family (Sonnet 5 / Opus 5 / Fable 5.1); `*` marks the family it actually ran on |
| `ACTUAL` | Real **mixed** cost — each model's tokens priced at its own rate |

The terminal table is ~157 chars wide (~174 with the PROJECT column). The cache-write
TTL split (5m / 1h), the raw `wall_seconds` / `active_seconds`, the subagent message
count (`subagent_messages`) next to the run count (`agent_runs`), the
subagent model list, and other per-session metadata are in the `--json` output.

> **Time window — defaults to today, configurable, counted per message.** By default
> every mode only counts messages from today (midnight → now, local time). Pass
> `--since` / `--until` for any other window: a span ago (`2d`, `2h`, `90m`), a clock
> time today (`15:00`, `3pm`), `today` / `yesterday`, or an ISO date/datetime.
>
> The window is applied **per assistant message**, by each message's own timestamp — not
> per session file. So a session that started before the window but kept running inside
> it contributes **only the tokens it spent inside the window**, not its whole-session
> total. A session with no in-window messages is dropped. (Messages lacking a timestamp
> fall back to the file's mtime.) `MSGS`, `AGENTS`, the token columns and all costs
> are in-window.

> **Cache is input-only.** The Claude API caches input (prompt) tokens, not output.
> There is no "output cache" — `CACHE-R` / `CACHE-W` are both input-side.

## How to run

Run the bundled script `scripts/report.py` from this skill's base directory (the folder
containing this SKILL.md — Claude Code shows it as the skill's base directory when the
skill loads; `<skill-dir>` below stands for it). Run it from the project directory you
want to analyze (it derives the project from the current working directory):

```bash
python3 <skill-dir>/scripts/report.py [-n N|all] [--project PATH] [--all-projects] [--since SPEC] [--until SPEC] [--sort recent|tokens] [--format FMT]
```

Flags:
- `-n, --number N` — how many sessions to include (default: **10**). Pass `all` (or `0`)
  for **no limit** — shows every session in the window.
- `--project PATH` — analyze a different project directory (default: current working dir).
- `--all-projects` — analyze **every** project under `~/.claude/projects`.
  The output gets a leading `PROJECT` column whenever more than one project shows up.
- `--since SPEC` — window start (default: today midnight). `--until SPEC` — window end
  (default: now). SPEC: `2d` / `2h` / `90m` (that span ago), `15:00` / `3pm` (today at
  that time), `today`, `yesterday`, `YYYY-MM-DD`, or an ISO datetime.
- `--sort {recent,tokens,cost}` — `recent` (default, newest first), `tokens` (largest
  token use first), or `cost` (highest actual mixed cost first). `tokens` and `cost`
  usually agree but can differ — e.g. a cheap-model session with huge token volume ranks
  high on `tokens` but lower on `cost`.
- `--idle-gap SECS` — idle threshold for the `ACTIVE` working-time estimate (default
  **300** = 5 min). Gaps between consecutive events longer than this are treated as the
  user being away and excluded from `ACTIVE`. Lower it (e.g. `--idle-gap 60`) for a
  stricter "hands-on-keyboard" estimate; it does not affect `WALL`.
- `--format {table,markdown,json}` — output format (default `table`). Shortcuts:
  `--markdown`, `--json`.
  - `table` — monospace ASCII table; **only aligns correctly in a real terminal**.
  - `markdown` — GitHub-flavored Markdown table; renders as a real table in a chat / IDE.
  - `json` — machine-readable, with the full per-session metadata (incl. `project`).

### When invoked as a skill (in the chat / IDE) — IMPORTANT

**The Bash tool's output is collapsed/hidden in the chat — the user does NOT see it.**
So running the script is not enough: if you only run it and then write a one-line
takeaway, the table is stuck inside the hidden Bash output and the user sees *nothing*.
This is the #1 failure mode of this skill.

Therefore, after running the script you **must copy its entire Markdown output verbatim
into the text of your own reply** — every line from the `**Token & cost report — …**`
header down to the final pricing note. Your message body is the only place the user
actually sees the table. Do not paraphrase it, do not drop the pricing tables, do not
wrap it in a code block (that would stop it rendering). Run with `--markdown` so the
output is already a GitHub-flavored table; paste it as-is, then add a one-line takeaway
*below* it.

Concretely: run the Bash command, take the stdout it produced, and reproduce that text
in your reply. If you find yourself replying with only a summary and no table, you have
made the mistake above — go back and paste the table.

```bash
python3 <skill-dir>/scripts/report.py -n 10 --markdown --project <cwd>
```

(The ASCII `table` format does not render in the chat — columns collapse without a
monospace font — which is why `--markdown` is mandatory here.)

Map the user's natural-language request to flags:

- **Default scope is the current project — always.** If the user does **not** explicitly
  ask about more than one project, run on the current project (`--project <cwd>`, no
  `--all-projects`). This holds no matter what else they ask for: a time window, a sort,
  a session count, "show all sessions", "mutasd az összes sessiont" — none of these imply
  multiple projects. "Sessions" (plural) means sessions of *this* project, not all projects.
- **Only use `--all-projects` when the user explicitly names the all-projects scope** —
  e.g. "all projects", "összes projekt", "minden projekt", "across all my projects",
  "melyik projekt vitte a legtöbb tokent". The word "összes/all" alone is ambiguous: in
  "mutasd az összeset" it refers to the **session count** (`-n all`), not the project
  scope. If in doubt, stay single-project and mention you can add `--all-projects` if they
  want every project. When >1 project shows up, the leading PROJECT column appears
  automatically.
- **Sorting** — default is `recent` (newest first). Map the phrasing:
  - "token szám szerint" / "legtöbb token" / "by tokens" → `--sort tokens`
  - "költség szerint" / "legdrágább" / "by cost" → `--sort cost`
  - bare "rendezve" / "nagyság szerint" / "sorted" (unspecified) → `--sort cost` (cost is
    the usual intent for a cost report; mention you can switch to `--sort tokens`).
- **A session count** → `-n N`. "all" / "összes" / "mindet" → `-n all` (no limit).
  "30-at" → `-n 30`. Default is `-n 10`.
- **A time window** (default: today). Map the phrasing to `--since` / `--until`:
  - "elmúlt két nap" / "last 2 days" → `--since 2d`
  - "elmúlt két óra" / "last 2 hours" → `--since 2h`
  - "mai nap három óra után" / "today after 3pm" → `--since 15:00` (or `3pm`)
  - "tegnap" / "yesterday" → `--since yesterday --until today`
  - "június 13. óta" → `--since 2026-06-13`

These compose: e.g. "összes projekt, rendezve, mutasd mindet, elmúlt két nap" →
`--all-projects --sort tokens -n all --since 2d --markdown`.

After the table, add a one-line takeaway (cheapest model for their usage, the heaviest
session, or which project burned the most today).

## How it works

- Locates the project's logs under `~/.claude/projects/<encoded-cwd>/` (Claude Code
  encodes the absolute path by replacing every non-alphanumeric char with `-`). Falls
  back to matching by trailing folder name if the exact dir isn't found.
- Sums token usage **per assistant message**, counting only messages whose timestamp
  falls inside the time window (default: today midnight → now; override with `--since` /
  `--until`). Files whose mtime predates the window start are skipped without parsing;
  sessions with no in-window messages are dropped. The surviving sessions are sorted — by
  recency (default) or by total token use (`--sort tokens`) — and the list is capped at N
  (or shown in full with `-n all`). With `--all-projects` it does this across every
  project dir and merges the results into one table.
- For every assistant message it reads `message.model` and `message.usage`, **including
  subagent calls**, and sums the token buckets. Subagents are counted from two places:
  inline `isSidechain: true` records in the session file, **and** the separate subagent
  transcripts under `<project>/<session-id>/` (e.g. the `.../subagents/*.jsonl` tree).
  The latter is where fan-out commands (book-summary, issue-fix, workflows) write their
  agents — they are NOT inline sidechain records, so a non-recursive glob would miss
  them entirely and undercount fan-out sessions by the bulk of their cost.
- **Usage is summed per API response, not per log record.** One response is written to
  the log once per content block (`thinking`, `tool_use`, `text`), and every one of those
  records repeats the **same cumulative** `usage`. Summing the records therefore counts
  the same tokens two or three times — on a tool-heavy session that roughly **doubles**
  the reported cost. So the records of one `message.id` are counted once. The input side
  (input, cache read, cache write) is identical across the records of one id; only
  `output_tokens` grows while the response streams, so a later record contributes just
  the output tokens that are new.
- The session's **main model** = the dominant model among non-sidechain messages.
- Metadata comes from the log too: the session **name** from the latest `ai-title`
  record (falling back to a truncated last prompt, then "untitled"); message counts from
  assistant records.
- **Timing** is derived from the timeline of in-window record timestamps (main session +
  subagent transcripts, merged so concurrent subagent work isn't double-counted).
  `WALL` = last event − first event (full elapsed span). `ACTIVE` = the sum of gaps
  between consecutive events, dropping any gap longer than `--idle-gap` (default 5 min) as
  idle. The logs store only one timestamp per event and **no true per-request duration**,
  so `ACTIVE` is an honest estimate from the timeline, not a measured generation time —
  this is why a session left open for hours can show a large `WALL` but a small `ACTIVE`.

## Pricing model

List pricing per 1M tokens. Source: the
[official pricing page](https://platform.claude.com/docs/en/about-claude/pricing),
checked 2026-09-06.

| Price key | Model | input | output | cache read |
|---|---|---|---|---|
| `sonnet-5` | Sonnet 5 | $2 | $10 | $0.20 |
| `sonnet-4` | Sonnet 4.5 / 4.6 | $3 | $15 | $0.30 |
| `opus-5` | Opus 5 | $5 | $25 | $0.50 |
| `opus-4` | Opus 4.5 – 4.8 | $5 | $25 | $0.50 |
| `opus-4-1` | Opus 4 / 4.1 (retired) | $15 | $75 | $1.50 |
| `fable-5-1` | Fable 5.1 / Mythos 5.1 | $10 | $50 | $0.25 |
| `fable-5` | Fable 5 / Mythos 5 | $10 | $50 | $1 |
| `haiku-4-5` | Haiku 4.5 | $1 | $5 | $0.10 |

**Pricing is version-aware.** A key is a price tier, not a model family. Versions of one
family no longer share a rate: Sonnet 5 costs less than Sonnet 4.6, and Fable 5.1 reads
its cache at 2.5% of the input price while every other model pays 10%. The script maps a
raw model id (`claude-opus-4-8[1m]`, `claude-haiku-4-5-20251001`) to a key with
`price_key()`; an id without a version (bare `sonnet`) gets the current model of that
family, and an unknown id is priced as Opus 5.

- Cache **reads** are billed at **10%** of the input rate — **2.5%** on Fable 5.1 and
  Mythos 5.1.
- Cache **writes** at **1.25×** (5-minute TTL) or **2×** (1-hour TTL) of the input rate
  — the logs carry the `ephemeral_5m` / `ephemeral_1h` breakdown, so this is exact.
- The three comparison columns reprice **all** of the session's tokens at the **current**
  model of that family ("what if everything ran on Sonnet 5 / Opus 5 / Fable 5.1").
- The **actual (mixed)** number prices each model's tokens at its own version's rate —
  this is the honest "what it actually cost", and differs from the marked column whenever
  subagents ran on a different model, or the main loop ran on an older version.

> **Update the rates when a new model ships.** Add or edit an entry in `RATES` in
> `scripts/report.py`, extend `price_key()` if the new id needs a new tier, and change
> `COMPARE` if the comparison columns must point at the new model.

> Effort (reasoning effort) is intentionally omitted: it is not recorded in the
> session logs, so it cannot be derived honestly.

> Cost is **API-equivalent**. Actual billing is covered by the Max/Pro subscription —
> these numbers exist to compare model burn per session, not to predict an invoice.
