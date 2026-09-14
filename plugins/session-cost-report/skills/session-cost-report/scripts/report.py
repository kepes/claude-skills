#!/usr/bin/env python3
"""Session token & cost report for Claude Code projects.

Reads today's session logs (.jsonl) of a project (or of EVERY project) from
~/.claude/projects/<encoded>/, sums token usage (including both inline sidechain
records AND the subagent transcripts that live in the separate
<project>/<session-id>/ subfolder), and prints a per-session token + cost summary.
Usage is summed per API response, NOT per log record: one response is logged once
per content block and every one of those records repeats the same cumulative usage,
so the records of one message id are counted once (see dedupe() below).
For each session it shows the API-equivalent cost on three models (Sonnet 5 /
Opus 5 / Fable 5.1), marks the session's real main model, and shows the actual
mixed cost (main model + subagents each at their own rate, version-aware).

The report is scoped to a time window — **today** by default (local midnight → now),
or any window via --since / --until. Sessions are matched by last activity (file mtime).

Usage:
    python3 report.py [-n N|all] [--project PATH] [--all-projects]
                      [--since SPEC] [--until SPEC] [--sort recent|tokens] [--json]

    -n, --number     How many recent sessions to include (default: 10).
                     Pass `all` (or `0`) for no limit.
    --project        Project dir to analyze (default: current working dir).
    --all-projects   Analyze every project under ~/.claude/projects.
                     The output gets a leading PROJECT column when >1 project shows up.
    --since SPEC     Window start (default: today midnight). SPEC: 2d/2h/90m (ago),
                     15:00/3pm (today), today, yesterday, YYYY-MM-DD, ISO datetime.
    --until SPEC     Window end (default: now). Same SPEC formats.
    --sort           recent (default, newest first) or tokens (largest token use first).
    --idle-gap SECS  Idle threshold for the ACTIVE working-time estimate (default 300).
    --format         table (ASCII, default) | markdown | json. Shortcuts: --markdown, --json.

Each session also reports two timings: WALL (wall-clock span, first→last event) and
ACTIVE (estimated working time = the span minus idle gaps longer than --idle-gap). The
logs store no true per-request duration, so ACTIVE is an estimate from the event timeline.

Cost is API-equivalent, estimated at list pricing (cache reads at 10% of input —
2.5% on Fable 5.1 / Mythos 5.1 —, cache writes at 1.25x for 5m TTL / 2x for 1h
TTL). Actual billing is covered by
the Max/Pro subscription — these numbers exist to compare model burn per session.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# --- Pricing: USD per single token (list price / 1e6) ---------------------------
# Source: platform.claude.com/docs/en/about-claude/pricing (checked 2026-09-06).
# A key is a PRICE TIER, not a family: Sonnet 5 costs less than Sonnet 4.6, Opus 4.1
# costs 3x Opus 5, and Fable 5.1 reads its cache at 2.5% of input while every other
# model pays 10%. "read" is the cache-read multiplier of that tier.
RATES = {
    "fable-5-1": {"in": 10.0 / 1e6, "out": 50.0 / 1e6, "read": 0.025,
                  "family": "fable", "label": "Fable 5.1"},
    "fable-5":   {"in": 10.0 / 1e6, "out": 50.0 / 1e6, "read": 0.10,
                  "family": "fable", "label": "Fable 5"},
    "opus-5":    {"in":  5.0 / 1e6, "out": 25.0 / 1e6, "read": 0.10,
                  "family": "opus", "label": "Opus 5"},
    "opus-4":    {"in":  5.0 / 1e6, "out": 25.0 / 1e6, "read": 0.10,
                  "family": "opus", "label": "Opus 4.5-4.8"},
    "opus-4-1":  {"in": 15.0 / 1e6, "out": 75.0 / 1e6, "read": 0.10,
                  "family": "opus", "label": "Opus 4/4.1"},
    "sonnet-5":  {"in":  2.0 / 1e6, "out": 10.0 / 1e6, "read": 0.10,
                  "family": "sonnet", "label": "Sonnet 5"},
    "sonnet-4":  {"in":  3.0 / 1e6, "out": 15.0 / 1e6, "read": 0.10,
                  "family": "sonnet", "label": "Sonnet 4.5/4.6"},
    "haiku-4-5": {"in":  1.0 / 1e6, "out":  5.0 / 1e6, "read": 0.10,
                  "family": "haiku", "label": "Haiku 4.5"},
}
DEFAULT_KEY = "opus-5"  # unknown / missing model id -> price as the current Opus
# Cache-write multipliers, relative to the model's input rate. The cache-READ
# multiplier is per tier: RATES[key]["read"].
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0

# Idle-gap threshold (seconds) for the "active" working-time estimate. The logs store
# only one timestamp per record (when it was written), not a true per-request duration,
# so "how long the LLM actually worked" can only be estimated from the timeline: we sum
# the gaps between consecutive in-window events and DROP any gap longer than this — those
# long stretches are the user reading / typing / away, not the model working. 5 minutes
# is a sane default boundary between "still churning" and "stepped away".
DEFAULT_IDLE_GAP_S = 300

# Columns shown in the comparison table (order = display order).
COMPARE = ["sonnet-5", "opus-5", "fable-5-1"]

PROJECTS_DIR = Path.home() / ".claude" / "projects"


# Version after the family name: 'opus-4-8' -> (4, 8), 'fable-5-1' -> (5, 1),
# 'haiku-4-5-20251001' -> (4, 5). The minor part takes at most 2 digits, so a date
# suffix ('opus-4-20250514') is not read as a minor version.
_VERSION_RE = re.compile(
    r"(?:fable|mythos|opus|sonnet|haiku)[-_ ]?(\d+)(?:[-.](\d{1,2})(?!\d))?"
)


def price_key(model_id: str) -> str | None:
    """Map a raw model id (e.g. 'claude-opus-4-8[1m]') to a pricing key.

    The mapping is version-aware, because versions of one family no longer share a
    price. An id without a version (bare 'sonnet', 'opus') is priced as the current
    model of that family."""
    if not model_id:
        return None
    m = model_id.lower()
    mv = _VERSION_RE.search(m)
    major = int(mv.group(1)) if mv else None
    minor = int(mv.group(2)) if mv and mv.group(2) else 0
    if "fable" in m or "mythos" in m:
        if major is not None and (major, minor) < (5, 1):
            return "fable-5"
        return "fable-5-1"
    if "opus" in m:
        if major is not None and major <= 4:
            return "opus-4-1" if minor <= 1 else "opus-4"
        return "opus-5"
    if "sonnet" in m:
        return "sonnet-4" if major is not None and major < 5 else "sonnet-5"
    if "haiku" in m:
        return "haiku-4-5"
    return None


def family_of(key: str) -> str:
    """Family (fable/opus/sonnet/haiku) of a price key — used for display only."""
    return RATES.get(key, {}).get("family", key)


def empty_bucket() -> dict:
    return {"in": 0, "read": 0, "w5": 0, "w1": 0, "out": 0}


def add_usage(bucket: dict, usage: dict) -> None:
    bucket["in"] += usage.get("input_tokens", 0) or 0
    bucket["read"] += usage.get("cache_read_input_tokens", 0) or 0
    bucket["out"] += usage.get("output_tokens", 0) or 0
    cc = usage.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        # No breakdown available; treat the lump cache_creation as 5m TTL.
        bucket["w5"] += usage.get("cache_creation_input_tokens", 0) or 0
    else:
        bucket["w5"] += w5 or 0
        bucket["w1"] += w1 or 0


def cost_of(bucket: dict, key: str) -> float:
    r = RATES[key]
    billable_in = (
        bucket["in"]
        + bucket["read"] * r["read"]
        + bucket["w5"] * CACHE_WRITE_5M_MULT
        + bucket["w1"] * CACHE_WRITE_1H_MULT
    )
    return billable_in * r["in"] + bucket["out"] * r["out"]


def total_tokens(bucket: dict) -> int:
    return bucket["in"] + bucket["read"] + bucket["w5"] + bucket["w1"] + bucket["out"]


def _usage_total(usage: dict) -> int:
    """Total raw tokens in a single usage record (for ranking subagent models)."""
    return (
        (usage.get("input_tokens", 0) or 0)
        + (usage.get("output_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0)
        + (usage.get("cache_creation_input_tokens", 0) or 0)
    )


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}m"


def wall_seconds(times: list[datetime]) -> int | None:
    """Wall-clock span of a session: last in-window event minus the first. This is the
    real elapsed time the session was open, including every pause while the user typed
    or stepped away."""
    if len(times) < 2:
        return 0 if times else None
    ts = sorted(times)
    return int((ts[-1] - ts[0]).total_seconds())


def active_seconds(times: list[datetime], idle_gap_s: int) -> int | None:
    """Estimated time the session was *actively working* (LLM generating, tools running,
    quick back-and-forth) — the wall-clock span minus the idle stretches. We sum the gaps
    between consecutive events but discard any gap longer than `idle_gap_s`, treating those
    as the user being away rather than the model working. This is an estimate, not a
    measured generation time: the logs don't record per-request durations, so the timeline
    of record timestamps is the only signal available."""
    if len(times) < 2:
        return 0 if times else None
    ts = sorted(times)
    total = 0.0
    for a, b in zip(ts, ts[1:]):
        gap = (b - a).total_seconds()
        if gap <= idle_gap_s:
            total += gap
    return int(total)


def cache_hit_rate(b: dict) -> float | None:
    """Share of input served from cache (read / all input processed)."""
    total_input = b["in"] + b["read"] + b["w5"] + b["w1"]
    if total_input == 0:
        return None
    return b["read"] / total_input


def encode_project_dir(path: Path) -> str:
    """Replicate Claude Code's project-folder encoding (non-alnum -> '-')."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path.resolve()))


def resolve_project_dir(project: str | None) -> Path:
    cwd = Path(project).resolve() if project else Path.cwd()
    encoded = encode_project_dir(cwd)
    candidate = PROJECTS_DIR / encoded
    if candidate.is_dir():
        return candidate
    # Fallback: match by trailing basename component.
    base = re.sub(r"[^a-zA-Z0-9]", "-", cwd.name)
    matches = sorted(
        (d for d in PROJECTS_DIR.iterdir() if d.is_dir() and d.name.endswith(base)),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = "\n  ".join(d.name for d in matches)
        sys.exit(
            f"Multiple project log dirs match '{cwd.name}':\n  {names}\n"
            f"Disambiguate with --project <full-path>."
        )
    sys.exit(
        f"No session logs found for project:\n  {cwd}\n"
        f"Looked for: {candidate}\n"
        f"List available projects in: {PROJECTS_DIR}"
    )


def all_project_dirs() -> list[Path]:
    """Every project log dir under ~/.claude/projects (top-level dirs only)."""
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted((d for d in PROJECTS_DIR.iterdir() if d.is_dir()))


def project_short_name(project_dir: Path) -> str:
    """Short, human-friendly label for a project's encoded log dir.

    Dirs are encoded paths like '-Users-alice-Work-...-myproject'; the trailing
    segment is usually the project folder name. Good enough for a table column."""
    name = project_dir.name.rstrip("-")
    seg = name.split("-")[-1] if name else name
    return seg or project_dir.name


def _parse_ts(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_local_naive(dt: datetime | None) -> datetime | None:
    """Convert a (possibly tz-aware, UTC) log timestamp to naive local time, so it can
    be compared against the naive-local window endpoints."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def parse_time_spec(spec: str, now: datetime) -> datetime:
    """Parse a time-window endpoint into a (naive, local) datetime.

    Accepted forms:
      - `now`                          -> the current moment
      - `today` / `yesterday`          -> that day's midnight (00:00 local)
      - duration ago: `2d`, `2h`, `90m`, combos like `1d6h`  -> now minus that span
      - clock time today: `15:00`, `15`, `3pm`, `9:30am`     -> today at that time
      - ISO date `YYYY-MM-DD`          -> that day's midnight
      - ISO datetime `YYYY-MM-DDTHH:MM`-> exact instant
    """
    s = spec.strip().lower()
    if not s:
        raise ValueError("empty time spec")
    if s == "now":
        return now
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Duration ago: one or more <N>d / <N>h / <N>m chunks (e.g. 2h, 90m, 1d6h).
    if re.fullmatch(r"(\d+\s*[dhm]\s*)+", s):
        total = timedelta()
        for num, unit in re.findall(r"(\d+)\s*([dhm])", s):
            n = int(num)
            total += {"d": timedelta(days=n), "h": timedelta(hours=n),
                      "m": timedelta(minutes=n)}[unit]
        return now - total
    # 12-hour clock with am/pm: 3pm, 9:30am.
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3) == "pm":
            h += 12
        return now.replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
    # 24-hour clock today: 15:00 or 15.
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", s)
    if m:
        h, minute = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return now.replace(hour=h, minute=minute, second=0, microsecond=0)
    # ISO date or datetime.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.fromisoformat(s).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return datetime.fromisoformat(spec).replace(tzinfo=None)
    except ValueError:
        pass
    raise ValueError(f"unrecognized time spec: {spec!r}")


def resolve_window(since: str | None, until: str | None, now: datetime):
    """Return (since_dt, until_dt, label). Default window is today (midnight → now)."""
    is_default = since is None and until is None
    try:
        since_dt = (parse_time_spec(since, now) if since
                    else now.replace(hour=0, minute=0, second=0, microsecond=0))
        until_dt = parse_time_spec(until, now) if until else now
    except ValueError as e:
        sys.exit(f"Bad time window: {e}")
    if since_dt > until_dt:
        sys.exit(f"Empty time window: --since ({since_dt:%Y-%m-%d %H:%M}) is after "
                 f"--until ({until_dt:%Y-%m-%d %H:%M}).")
    if is_default:
        label = "today"
    else:
        same_day = since_dt.date() == until_dt.date()
        if same_day:
            label = f"{since_dt:%Y-%m-%d %H:%M}–{until_dt:%H:%M}"
        else:
            label = f"{since_dt:%Y-%m-%d %H:%M} → {until_dt:%Y-%m-%d %H:%M}"
    return since_dt, until_dt, label


def parse_number(val: str | None) -> int | None:
    """Parse the -n value: a positive int caps the list; 'all'/'összes'/0 -> no cap."""
    if val is None:
        return 10
    s = str(val).strip().lower()
    if s in ("all", "összes", "osszes", "mind", "minden", "*"):
        return None
    try:
        n = int(s)
    except ValueError:
        sys.exit(f"--number must be an integer or 'all' (got: {val!r})")
    return None if n <= 0 else n


def _iter_records(path: Path):
    """Yield parsed JSON records from a .jsonl file, skipping unreadable lines."""
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _subagent_files(jsonl_path: Path) -> list[Path]:
    """Subagent transcripts for a session live in <project>/<session-id>/ (e.g. the
    .../subagents/*.jsonl tree), as SEPARATE files — not inline sidechain records.
    The main top-level glob is non-recursive, so it never sees them; we pull them in
    explicitly here, otherwise fan-out sessions (book-summary, issue-fix, workflows)
    are massively undercounted."""
    subdir = jsonl_path.with_suffix("")  # <project>/<session-id>/
    if not subdir.is_dir():
        return []
    return sorted(subdir.rglob("*.jsonl"))


def analyze_session(jsonl_path: Path, since_dt: datetime, until_dt: datetime,
                    idle_gap_s: int = DEFAULT_IDLE_GAP_S) -> dict:
    """Parse one session .jsonl into token buckets and metadata, counting ONLY the
    assistant messages whose own timestamp falls inside [since_dt, until_dt].

    This is what makes a windowed report honest: a session that started before the
    window but kept running inside it contributes only the tokens it spent inside the
    window — not its whole-session total. Records without a usable timestamp fall back
    to the file's mtime (a reasonable proxy for "when this was written").

    Token usage covers the main session file (incl. inline sidechain records) AND the
    session's separate subagent transcripts under <project>/<session-id>/."""
    session_bucket = empty_bucket()       # in-window tokens, all models
    key_buckets: dict[str, dict] = {}  # per price tier (for the actual mixed cost)
    main_model_tokens: dict[str, int] = {}  # main-loop tier -> output tokens (for dominance)
    sub_key_tokens: dict[str, int] = {}  # subagent tier -> total tokens (for the MODEL cell)
    win_first: datetime | None = None     # first/last in-window message time (for duration)
    win_last: datetime | None = None
    event_times: list[datetime] = []      # every in-window record time (main + subagents),
                                          # used for the wall-clock span and active-time est.
    title: str | None = None
    last_prompt: str | None = None
    n_assistant = 0
    n_subagent = 0
    n_agents = 0

    try:
        file_mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
    except OSError:
        file_mtime = until_dt

    def in_window(ts_str: str | None, fallback: datetime):
        """(record_time, is_inside_window). Missing timestamp -> fallback (file mtime)."""
        rt = _to_local_naive(_parse_ts(ts_str)) or fallback
        return rt, (since_dt <= rt <= until_dt)

    counted_out: dict[str, int] = {}  # message id -> output tokens already counted

    def dedupe(msg: dict) -> tuple[dict | None, bool]:
        """Guard against the same API response being counted more than once.

        One response is written to the log once per content block (thinking, tool_use,
        text), and EVERY one of those records repeats the SAME cumulative usage. Summing
        the records doubles the totals, so a message id is counted once. The input side
        (input, cache read, cache write) is identical across the records of one id; only
        output_tokens grows while the response streams, so a later record contributes
        just the output tokens that are new.

        Returns (usage_to_count | None, is_new_message)."""
        usage = msg["usage"]
        mid = msg.get("id")
        if not mid:
            return usage, True  # nothing to deduplicate on -> count the record as-is
        out = usage.get("output_tokens", 0) or 0
        if mid not in counted_out:
            counted_out[mid] = out
            return usage, True
        if out <= counted_out[mid]:
            return None, False  # a repeat of an already-counted response
        delta = out - counted_out[mid]
        counted_out[mid] = out
        return {"output_tokens": delta}, False

    def count(usage: dict, key: str, is_sub: bool) -> None:
        add_usage(session_bucket, usage)
        add_usage(key_buckets.setdefault(key, empty_bucket()), usage)
        if is_sub:
            sub_key_tokens[key] = sub_key_tokens.get(key, 0) + _usage_total(usage)
        else:
            main_model_tokens[key] = main_model_tokens.get(key, 0) + (
                usage.get("output_tokens", 0) or 0
            )

    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Timeline: record every event with a real in-window timestamp (any role/type,
            # not just assistant), so wall-clock + active time reflect the whole session.
            rec_t = _to_local_naive(_parse_ts(rec.get("timestamp")))
            if rec_t is not None and since_dt <= rec_t <= until_dt:
                event_times.append(rec_t)
            # One agent run = one sidechain chain root (parentUuid is null). Inline
            # subagents write their records into the main session file, so a chain
            # root there marks an agent that started inside the window.
            if rec.get("isSidechain") and not rec.get("parentUuid"):
                root_t = rec_t if rec_t is not None else file_mtime
                if since_dt <= root_t <= until_dt:
                    n_agents += 1
            rtype = rec.get("type")
            if rtype == "ai-title" and rec.get("aiTitle"):
                title = rec["aiTitle"]  # keep the latest title (session-level metadata)
                continue
            if rtype == "last-prompt" and rec.get("lastPrompt"):
                last_prompt = rec["lastPrompt"]
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            rt, inside = in_window(rec.get("timestamp"), file_mtime)
            if not inside:
                continue
            u, is_new = dedupe(msg)
            if u is None:
                continue
            if win_first is None:
                win_first = rt
            win_last = rt
            is_sub = bool(rec.get("isSidechain"))
            if is_new:
                n_assistant += 1
                if is_sub:
                    n_subagent += 1
            key = price_key(msg.get("model", "")) or DEFAULT_KEY
            count(u, key, is_sub)

    # Separate subagent transcript files (<project>/<session-id>/...): count their
    # in-window tokens too, always as subagents. They never affect main-model dominance.
    for sub_path in _subagent_files(jsonl_path):
        try:
            sub_mtime = datetime.fromtimestamp(sub_path.stat().st_mtime)
        except OSError:
            sub_mtime = file_mtime
        sub_active = False  # True if this transcript has an in-window assistant record
        for rec in _iter_records(sub_path):
            rec_t = _to_local_naive(_parse_ts(rec.get("timestamp")))
            if rec_t is not None and since_dt <= rec_t <= until_dt:
                event_times.append(rec_t)  # subagent events share the session timeline
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            rt, inside = in_window(rec.get("timestamp"), sub_mtime)
            if not inside:
                continue
            sub_active = True
            u, is_new = dedupe(msg)
            if u is None:
                continue
            if win_first is None:
                win_first = rt
            win_last = rt
            if is_new:
                n_assistant += 1
                n_subagent += 1
            key = price_key(msg.get("model", "")) or DEFAULT_KEY
            count(u, key, is_sub=True)
        if sub_active:
            n_agents += 1  # one transcript file = one agent run

    main_key = (
        max(main_model_tokens, key=main_model_tokens.get)
        if main_model_tokens
        else DEFAULT_KEY
    )
    actual_cost = sum(cost_of(b, k) for k, b in key_buckets.items())

    # Models used, ordered by total token volume (descending).
    models_used = [
        RATES[k]["label"]
        for k in sorted(
            key_buckets, key=lambda x: total_tokens(key_buckets[x]), reverse=True
        )
    ]

    # Subagent price tiers, ordered by their token volume (for the MODEL cell).
    sub_keys = sorted(sub_key_tokens, key=lambda k: sub_key_tokens[k], reverse=True)

    # Timing. Wall-clock = full span of the session inside the window; active = that span
    # with idle stretches (gaps > idle_gap_s) removed — the closest honest proxy for "how
    # long the LLM actually worked". Fall back to the assistant-message span if, unusually,
    # no records carried a real timestamp.
    if not event_times and win_first and win_last:
        event_times = [win_first, win_last]
    wall_s = wall_seconds(event_times)
    active_s = active_seconds(event_times, idle_gap_s)
    duration_s = wall_s  # back-compat alias (JSON 'duration_seconds')

    if not title:
        title = (last_prompt[:60].strip() + "…") if last_prompt else "(untitled session)"

    return {
        "session_id": jsonl_path.stem,
        "title": title,
        "first_ts": win_first.isoformat() if win_first else None,
        "last_ts": win_last.isoformat() if win_last else None,
        "mtime": jsonl_path.stat().st_mtime,
        "duration_s": duration_s,
        "wall_s": wall_s,
        "active_s": active_s,
        "n_assistant": n_assistant,
        "n_subagent": n_subagent,
        "n_agents": n_agents,
        "models_used": models_used,
        "sub_keys": sub_keys,
        "bucket": session_bucket,
        "key_buckets": key_buckets,
        "main_key": main_key,
        "compare": {k: cost_of(session_bucket, k) for k in COMPARE},
        "actual_cost": actual_cost,
    }


def collect_sessions(project_dir: Path, since_dt: datetime, until_dt: datetime,
                     idle_gap_s: int = DEFAULT_IDLE_GAP_S) -> list[dict]:
    """Analyze the project's sessions, counting only messages inside [since_dt, until_dt].

    A file whose mtime is before the window start cannot hold any in-window message, so
    we skip it without parsing. Anything else is parsed and the per-message window filter
    (in analyze_session) does the rest; sessions with no in-window activity are dropped."""
    sessions = []
    for p in project_dir.glob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if mtime < since_dt:
            continue  # last write predates the window -> nothing in range
        s = analyze_session(p, since_dt, until_dt, idle_gap_s)
        if s["n_assistant"] == 0:
            continue  # no assistant messages fell inside the window
        s["project_dir"] = str(project_dir)
        s["project"] = project_short_name(project_dir)
        sessions.append(s)
    return sessions


def fmt_ts(s: dict) -> str:
    ts = s["last_ts"]
    if ts:
        return ts.replace("T", " ")[:16]
    from datetime import datetime

    return datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")


SHORT = {"fable": "Fable", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}

# (header, width, align) — the report's core columns (without the optional PROJECT col).
BASE_COLUMNS = [
    ("NAME", 30, "l"),
    ("MODEL", 22, "l"),
    ("MSGS", 5, "r"),
    ("AGENTS", 6, "r"),
    ("WALL", 6, "r"),
    ("ACTIVE", 6, "r"),
    ("IN", 7, "r"),
    ("OUT", 7, "r"),
    ("CACHE-R", 8, "r"),
    ("CACHE-W", 8, "r"),
    ("HIT", 5, "r"),
    ("SONNET", 8, "r"),
    ("OPUS", 8, "r"),
    ("FABLE", 8, "r"),
    ("ACTUAL", 9, "r"),
]
PROJECT_COLUMN = ("PROJECT", 16, "l")


def build_columns(show_project: bool) -> list[tuple]:
    return ([PROJECT_COLUMN] + BASE_COLUMNS) if show_project else list(BASE_COLUMNS)


def _trunc(text: str, w: int) -> str:
    return text if len(text) <= w else text[: w - 1] + "…"


def _render_row(values: list[str], cols: list[tuple]) -> str:
    out = []
    for (_, w, align), v in zip(cols, values):
        v = _trunc(str(v), w)
        out.append(v.ljust(w) if align == "l" else v.rjust(w))
    return " ".join(out)


def _name_cell(title: str, session_id: str, w: int) -> str:
    suffix = f" ({session_id[:8]})"
    avail = w - len(suffix)
    return _trunc(title, avail) + suffix


def _short_of(key: str) -> str:
    """Short display name of a price key: 'sonnet-5' -> 'Sonnet'."""
    fam = family_of(key)
    return SHORT.get(fam, fam)


def _model_cell(s: dict) -> str:
    """Main model, with the subagent models in parentheses, e.g. 'Sonnet (Opus)'.

    Two tiers of one family (Fable 5 and Fable 5.1) share a short name, so the
    subagent list is deduplicated."""
    label = _short_of(s["main_key"])
    sub_labels: list[str] = []
    for key in s.get("sub_keys") or []:
        name = _short_of(key)
        if name not in sub_labels:
            sub_labels.append(name)
    if sub_labels:
        return f"{label} ({', '.join(sub_labels)})"
    return label


def _cost_cell(s: dict, key: str) -> str:
    """The what-if cost cell; '*' marks the family the session actually ran on."""
    val = fmt_usd(s["compare"][key])
    return val + "*" if family_of(key) == family_of(s["main_key"]) else val


def _core_row(s: dict, name_width: int) -> list[str]:
    """The non-PROJECT cells of a session row (table format)."""
    b = s["bucket"]
    hr = cache_hit_rate(b)
    return [
        _name_cell(s["title"], s["session_id"], name_width),
        _model_cell(s),
        s["n_assistant"],
        s["n_agents"],
        fmt_duration(s["wall_s"]),
        fmt_duration(s["active_s"]),
        fmt_tokens(b["in"]),
        fmt_tokens(b["out"]),
        fmt_tokens(b["read"]),
        fmt_tokens(b["w5"] + b["w1"]),
        f"{hr * 100:.0f}%" if hr is not None else "-",
        _cost_cell(s, "sonnet-5"),
        _cost_cell(s, "opus-5"),
        _cost_cell(s, "fable-5-1"),
        fmt_usd(s["actual_cost"]),
    ]


def print_report(sessions: list[dict], scope_label: str, show_project: bool,
                 window_label: str = "today") -> None:
    cols = build_columns(show_project)
    table_width = sum(w for _, w, _ in cols) + (len(cols) - 1)
    name_width = BASE_COLUMNS[0][1]

    print()
    print(f"Token & cost report — {scope_label}")
    print(f"{len(sessions)} session(s) · {window_label}")
    print()

    print(_render_row([h for h, _, _ in cols], cols))
    print("-" * table_width)

    for s in sessions:
        row = _core_row(s, name_width)
        if show_project:
            row = [s.get("project", "")] + row
        print(_render_row(row, cols))

    # Totals row
    tb = empty_bucket()
    for s in sessions:
        for k in tb:
            tb[k] += s["bucket"][k]
    thr = cache_hit_rate(tb)
    tot_cmp = {k: sum(s["compare"][k] for s in sessions) for k in COMPARE}
    tot_actual = sum(s["actual_cost"] for s in sessions)
    tot_wall = sum(s["wall_s"] for s in sessions if s["wall_s"] is not None)
    tot_active = sum(s["active_s"] for s in sessions if s["active_s"] is not None)
    total_core = [
        f"TOTAL ({len(sessions)})",
        "",
        sum(s["n_assistant"] for s in sessions),
        sum(s["n_agents"] for s in sessions),
        fmt_duration(tot_wall),
        fmt_duration(tot_active),
        fmt_tokens(tb["in"]),
        fmt_tokens(tb["out"]),
        fmt_tokens(tb["read"]),
        fmt_tokens(tb["w5"] + tb["w1"]),
        f"{thr * 100:.0f}%" if thr is not None else "-",
        fmt_usd(tot_cmp["sonnet-5"]),
        fmt_usd(tot_cmp["opus-5"]),
        fmt_usd(tot_cmp["fable-5-1"]),
        fmt_usd(tot_actual),
    ]
    if show_project:
        total_core = [""] + total_core
    print("-" * table_width)
    print(_render_row(total_core, cols))

    print()
    print("Columns: AGENTS = subagent runs started in the window · WALL = wall-clock")
    print("span (first→last event) · ACTIVE = est. working")
    print("time (span minus idle gaps >5m — approx., logs have no true request duration) ·")
    print("IN/OUT = uncached input / output tokens · CACHE-R/CACHE-W = cache read / write")
    print("(input-side only; no output cache) · HIT = cache read as % of input ·")
    print("SONNET/OPUS/FABLE = est. cost if the whole session ran on that model")
    print("(* = the model it actually ran on) · ACTUAL = real mixed cost (per-model rates).")
    print(f"Window: {window_label}. Cache-write TTL split (5m/1h) and per-session metadata are in --json.")
    print()
    print("Pricing used (USD per 1M tokens; input/output priced separately):")
    pcols = [("MODEL", 12, "l"), ("INPUT", 9, "r"), ("OUTPUT", 9, "r"),
             ("CACHE-R", 9, "r"), ("CW-5m", 9, "r"), ("CW-1h", 9, "r")]

    def prow(vals):
        return " ".join(
            (_trunc(str(v), w).ljust(w) if a == "l" else _trunc(str(v), w).rjust(w))
            for (_, w, a), v in zip(pcols, vals)
        )
    print(prow([h for h, _, _ in pcols]))
    for k in COMPARE:
        r = RATES[k]
        inp, out = r["in"] * 1e6, r["out"] * 1e6
        print(prow([
            r["label"], f"${inp:.2f}", f"${out:.2f}",
            f"${inp * r['read']:.2f}",
            f"${inp * CACHE_WRITE_5M_MULT:.2f}",
            f"${inp * CACHE_WRITE_1H_MULT:.2f}",
        ]))
    print()
    print("Cost = input×in-rate + output×out-rate + cache-read×(10% in, 2.5% on "
          "Fable 5.1) + cache-write×(1.25–2× in). List pricing; billing is")
    print("covered by your subscription.")


def to_json(sessions: list[dict], scope_label: str, show_project: bool,
            window_label: str = "today") -> str:
    out = {
        "scope": scope_label,
        "window": window_label,
        "multi_project": show_project,
        "sessions": [
            {
                "project": s.get("project"),
                "session_id": s["session_id"],
                "title": s["title"],
                "timestamp": s["last_ts"],
                "duration_seconds": s["duration_s"],
                "wall_seconds": s["wall_s"],
                "active_seconds": s["active_s"],
                "assistant_messages": s["n_assistant"],
                "subagent_messages": s["n_subagent"],
                "agent_runs": s["n_agents"],
                "models_used": s["models_used"],
                "main_model": RATES[s["main_key"]]["label"],
                "subagent_models": [RATES[k]["label"] for k in s["sub_keys"]],
                "tokens": s["bucket"],
                "total_tokens": total_tokens(s["bucket"]),
                "cache_hit_rate": (
                    round(cache_hit_rate(s["bucket"]), 4)
                    if cache_hit_rate(s["bucket"]) is not None else None
                ),
                "cost_by_model": {RATES[k]["label"]: round(s["compare"][k], 4) for k in COMPARE},
                "actual_cost": round(s["actual_cost"], 4),
            }
            for s in sessions
        ],
        "totals": {
            "cost_by_model": {
                RATES[k]["label"]: round(sum(s["compare"][k] for s in sessions), 4)
                for k in COMPARE
            },
            "actual_cost": round(sum(s["actual_cost"] for s in sessions), 4),
        },
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def to_markdown(sessions: list[dict], scope_label: str, show_project: bool,
                window_label: str = "today") -> str:
    """GitHub-flavored Markdown table — renders as a real table in chat/IDE."""
    head = [
        "Session", "Model", "Msgs", "Agents", "Wall", "Active", "In", "Out",
        "Cache-R", "Cache-W", "Hit", "Sonnet", "Opus", "Fable", "Actual",
    ]
    align = ["---", "---", "--:", "--:", "--:", "--:", "--:", "--:", "--:",
             "--:", "--:", "--:", "--:", "--:", "--:"]
    if show_project:
        head = ["Project"] + head
        align = ["---"] + align
    lines = [
        f"**Token & cost report — {scope_label}** · {len(sessions)} session(s) · {window_label}",
        "",
        "| " + " | ".join(head) + " |",
        "| " + " | ".join(align) + " |",
    ]

    def cost_md(s: dict, key: str) -> str:
        v = fmt_usd(s["compare"][key])
        return f"**{v}**" if family_of(key) == family_of(s["main_key"]) else v

    for s in sessions:
        b = s["bucket"]
        hr = cache_hit_rate(b)
        name = s["title"].replace("|", "\\|")
        if len(name) > 45:
            name = name[:44] + "…"
        row = [
            f"{name} (`{s['session_id'][:8]}`)",
            _model_cell(s),
            str(s["n_assistant"]),
            str(s["n_agents"]),
            fmt_duration(s["wall_s"]),
            fmt_duration(s["active_s"]),
            fmt_tokens(b["in"]),
            fmt_tokens(b["out"]),
            fmt_tokens(b["read"]),
            fmt_tokens(b["w5"] + b["w1"]),
            f"{hr * 100:.0f}%" if hr is not None else "–",
            cost_md(s, "sonnet-5"),
            cost_md(s, "opus-5"),
            cost_md(s, "fable-5-1"),
            fmt_usd(s["actual_cost"]),
        ]
        if show_project:
            row = [s.get("project", "")] + row
        lines.append("| " + " | ".join(row) + " |")

    # Totals
    tb = empty_bucket()
    for s in sessions:
        for k in tb:
            tb[k] += s["bucket"][k]
    thr = cache_hit_rate(tb)
    tot_cmp = {k: sum(s["compare"][k] for s in sessions) for k in COMPARE}
    tot_actual = sum(s["actual_cost"] for s in sessions)
    tot_wall = sum(s["wall_s"] for s in sessions if s["wall_s"] is not None)
    tot_active = sum(s["active_s"] for s in sessions if s["active_s"] is not None)
    total_row = [
        f"**TOTAL ({len(sessions)})**", "",
        f"**{sum(s['n_assistant'] for s in sessions)}**",
        f"**{sum(s['n_agents'] for s in sessions)}**",
        f"**{fmt_duration(tot_wall)}**", f"**{fmt_duration(tot_active)}**",
        f"**{fmt_tokens(tb['in'])}**", f"**{fmt_tokens(tb['out'])}**",
        f"**{fmt_tokens(tb['read'])}**", f"**{fmt_tokens(tb['w5'] + tb['w1'])}**",
        f"**{thr * 100:.0f}%**" if thr is not None else "–",
        f"**{fmt_usd(tot_cmp['sonnet-5'])}**", f"**{fmt_usd(tot_cmp['opus-5'])}**",
        f"**{fmt_usd(tot_cmp['fable-5-1'])}**", f"**{fmt_usd(tot_actual)}**",
    ]
    if show_project:
        total_row = [""] + total_row
    lines.append("| " + " | ".join(total_row) + " |")

    lines += [
        "",
        "_Agents = subagent runs started in the window. "
        "Wall = wall-clock span (first→last event). Active = est. working time "
        "(span minus idle gaps >5m) — approximate, the logs hold no true per-request "
        "duration. Sonnet/Opus/Fable = est. cost if the whole session ran on that model "
        "(**bold** = the model it actually ran on). Actual = real mixed cost. "
        f"Window: {window_label}. Cache is input-only (no output cache); Hit = cache read as % of input. "
        "API-equivalent list pricing — actual billing is covered by your subscription._",
        "",
        "**Pricing used** (USD per 1M tokens — input and output priced separately; "
        "cache read = 10% of input — 2.5% on Fable 5.1 —, cache write = 1.25× input "
        "for 5m TTL / 2× for 1h TTL):",
        "",
        "| Model | Input | Output | Cache read | Cache write 5m | Cache write 1h |",
        "| --- | --: | --: | --: | --: | --: |",
    ]
    for k in COMPARE:
        r = RATES[k]
        inp, out = r["in"] * 1e6, r["out"] * 1e6
        lines.append(
            f"| {r['label']} | ${inp:.2f} | ${out:.2f} | "
            f"${inp * r['read']:.2f} | "
            f"${inp * CACHE_WRITE_5M_MULT:.2f} | "
            f"${inp * CACHE_WRITE_1H_MULT:.2f} |"
        )
    lines.append(
        "\n_Cost per session = input×in-rate + output×out-rate + "
        "cache-read×(10% in-rate, 2.5% on Fable 5.1) + "
        "cache-write×(1.25–2× in-rate)._"
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-session token & cost report (today).")
    ap.add_argument("-n", "--number", default="10",
                    help="How many sessions (default: 10). Pass 'all' (or 0) for no limit.")
    ap.add_argument("--project", default=None,
                    help="Project dir to analyze (default: cwd)")
    ap.add_argument("--all-projects", action="store_true",
                    help="Analyze every project under ~/.claude/projects")
    ap.add_argument("--since", default=None,
                    help="Window start: 2d / 2h / 90m (ago), 15:00 / 3pm (today), "
                         "today, yesterday, YYYY-MM-DD. Default: today midnight.")
    ap.add_argument("--until", default=None,
                    help="Window end (same formats as --since). Default: now.")
    ap.add_argument("--sort", choices=["recent", "tokens", "cost"], default="recent",
                    help="recent (default, newest first), tokens (largest token use first), "
                         "or cost (highest actual cost first)")
    ap.add_argument("--idle-gap", type=int, default=DEFAULT_IDLE_GAP_S,
                    help="Idle threshold in seconds for the ACTIVE working-time estimate: "
                         f"gaps longer than this are excluded as idle (default: {DEFAULT_IDLE_GAP_S}).")
    ap.add_argument("--format", choices=["table", "markdown", "json"], default="table",
                    help="Output format: table (ASCII, default), markdown, or json")
    ap.add_argument("--markdown", dest="format", action="store_const", const="markdown",
                    help="Shortcut for --format markdown")
    ap.add_argument("--json", dest="format", action="store_const", const="json",
                    help="Shortcut for --format json")
    args = ap.parse_args()

    number = parse_number(args.number)
    since_dt, until_dt, window_label = resolve_window(args.since, args.until, datetime.now())

    if args.all_projects:
        sessions: list[dict] = []
        for pdir in all_project_dirs():
            sessions.extend(collect_sessions(pdir, since_dt, until_dt, args.idle_gap))
        scope_label = "all projects"
    else:
        project_dir = resolve_project_dir(args.project)
        sessions = collect_sessions(project_dir, since_dt, until_dt, args.idle_gap)
        scope_label = project_dir.name

    if not sessions:
        sys.exit(f"No session activity in window ({window_label})"
                 + (" in any project." if args.all_projects else f" in {scope_label}."))

    # Sort: newest-first (recency), largest-token-use-first, or highest-cost-first.
    if args.sort == "tokens":
        sessions.sort(key=lambda s: total_tokens(s["bucket"]), reverse=True)
    elif args.sort == "cost":
        sessions.sort(key=lambda s: s["actual_cost"], reverse=True)
    else:
        sessions.sort(key=lambda s: s["mtime"], reverse=True)

    if number is not None:
        sessions = sessions[:number]

    # PROJECT column only when more than one distinct project shows up.
    show_project = len({s.get("project") for s in sessions}) > 1

    if args.format == "json":
        print(to_json(sessions, scope_label, show_project, window_label))
    elif args.format == "markdown":
        print(to_markdown(sessions, scope_label, show_project, window_label))
    else:
        print_report(sessions, scope_label, show_project, window_label)


if __name__ == "__main__":
    main()
