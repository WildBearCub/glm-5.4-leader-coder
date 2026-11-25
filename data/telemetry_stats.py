#!/usr/bin/env python3
"""Recompute the Leader/coder telemetry tables from local ZCode data.

Two data sources, both local to the machine that ran the sessions:

  1. Telemetry logs: <zcode-home>/cli/log/zcode-YYYY-MM-DD.jsonl
     One JSON line per event. Token fields are redacted here ("[Redacted]"),
     but every completed model response carries `reasoningDeltaChars` in
     clear text, and every tool call carries the agent type and tool name.

  2. The usage database: <zcode-home>/cli/db/db.sqlite, table `model_usage`
     One row per model request with real token counts by model. Opened
     read-only; only aggregate columns are read, no conversation content.
     In this database `input_tokens` is the whole prompt, cached part
     included (`cache_read_input_tokens` is the cached share of it), and
     `output_tokens` includes reasoning. The `reasoning_tokens` column is
     not filled in for this provider, so reasoning volume is read from the
     logs instead.

Usage:
    # tables built from the logs only (reasoning characters, tool calls)
    python telemetry_stats.py <log-dir> <out-dir>

    # adds the session-level token comparison (needs the usage database)
    python telemetry_stats.py <log-dir> <out-dir> --usage-db <db.sqlite> \
        --compare <group>/<label>=<session-id> [<group>/<label>=<session-id> ...] \
        [--until <time>] [--split <time>] [--window <time> <time>]

Times are local time, written YYYY-MM-DDTHH:MM. --until ignores everything
that started after that moment, which makes a snapshot reproducible while
a session is still running. --split reports the coder's reasoning volume
before and after a moment. --window reports real tokens per model for
requests started inside a time window.

A session id may be given as any unique prefix, with or without the leading
"sess_". Output files carry the labels only, never the session ids.

Outputs in <out-dir>:
    by_model_role.csv        one row per (model, querySource)       [logs]
    by_day.csv               one row per (day, model, querySource)  [logs]
    tool_calls.csv           tool calls per day per agent type      [logs]
    summary.json             headline numbers used in README.md
    compare_sessions.csv     one row per compared session           [--compare]
    compare_tokens_long.csv  one row per (session, model)           [--compare]
    compare_groups.csv       pooled per group, per model            [--compare]
    compare_scenarios.csv    quota-weighted tokens per edit under
                             three assumptions about cached input   [--compare]
    dispatch_overhead.csv    one row per coder subagent             [--compare]
    window_tokens.csv        tokens per model inside the window     [--window]

Stdlib only.
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import pathlib
import sqlite3

SUBAGENT_PREFIX = "sess_subagent_"
EDIT_TOOLS = ("Edit", "Write")
STRONG, CHEAP, BLIND = "GLM-5.4", "GLM-5.3-Flash", "GLM-Fable"
MODEL_ALIASES = (("strong", STRONG), ("cheap", CHEAP), ("blind", BLIND))
STAMP = "%Y-%m-%dT%H:%M"
BUCKET_KEYS = ["requests", "input_tokens", "cache_read_tokens", "cache_creation_tokens",
               "output_tokens", "reasoning_tokens"]

# Quota burn rate relative to GLM-5.4, per token. GLM-5.3-Flash is the
# vendor's published figure (about 1/4 of GLM-5.4 for the same tokens).
# GLM-Fable is set equal to GLM-5.4 on the author's understanding that it is
# a blind-listed GLM-5.4. The telemetry cannot confirm that, so GLM-Fable is
# kept as its own row everywhere and never merged into the GLM-5.4 figures.
RATE = {STRONG: 1.0, BLIND: 1.0, CHEAP: 0.25}

# How the plan counts cached input tokens is not published, so every
# quota-weighted figure is given under three stated assumptions.
SCENARIOS = ("all tokens at face value", "cache reads at 10%", "output tokens only")


def quantile(values, p):
    if not values:
        return 0
    v = sorted(values)
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def median(values):
    return quantile(values, 0.5)


def n(v):
    return v or 0


def new_bucket():
    return {k: 0 for k in BUCKET_KEYS}


EMPTY = new_bucket()   # read-only stand-in for a model a session never used


def add(bucket, row):
    bucket["requests"] += 1
    bucket["input_tokens"] += n(row["input"])
    bucket["cache_read_tokens"] += n(row["cache_read"])
    bucket["cache_creation_tokens"] += n(row["cache_creation"])
    bucket["output_tokens"] += n(row["output"])
    bucket["reasoning_tokens"] += n(row["reasoning"])


def quota_units(bucket, rate, scenario):
    """GLM-5.4-equivalent tokens for one model's usage under one assumption."""
    if rate is None:
        return None
    uncached = max(0, bucket["input_tokens"] - bucket["cache_read_tokens"] - bucket["cache_creation_tokens"])
    if scenario == "cache reads at 10%":
        tokens = (uncached + 0.1 * bucket["cache_read_tokens"] + bucket["cache_creation_tokens"]
                  + bucket["output_tokens"])
    elif scenario == "output tokens only":
        tokens = bucket["output_tokens"]
    else:
        tokens = bucket["input_tokens"] + bucket["output_tokens"]
    return tokens * rate


def to_epoch_ms(ts):
    if not ts:
        return None
    try:
        return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def local_ms(text):
    return int(datetime.datetime.strptime(text, STAMP).timestamp() * 1000)


def local_stamp(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime(STAMP)


def open_read_only(db_path):
    return sqlite3.connect("file:" + pathlib.Path(db_path).as_posix() + "?mode=ro", uri=True, timeout=10)


def write_csv(path, rows):
    if not rows:
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def dump_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- logs

def load_logs(log_dir, until_ms=None):
    events, calls, agent_parent = [], [], {}
    for path in sorted(glob.glob(os.path.join(log_dir, "zcode-*.jsonl"))):
        day = os.path.basename(path)[len("zcode-"):-len(".jsonl")]
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"model.sdk.stream.completed"' not in line and '"tool.call.started"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ctx = rec.get("context") or {}
                ms = to_epoch_ms(rec.get("timestamp"))
                if until_ms is not None and ms is not None and ms > until_ms:
                    continue
                if rec.get("event") == "tool.call.started":
                    aid = ctx.get("agentId")
                    calls.append({
                        "day": day, "ms": ms, "sess": rec.get("sessionId", ""),
                        "at": ctx.get("agentType") or "main", "aid": aid,
                        "ps": ctx.get("parentSessionId"), "tool": ctx.get("toolName"),
                    })
                    if aid and ctx.get("parentSessionId"):
                        agent_parent[aid] = ctx["parentSessionId"]
                elif rec.get("event") == "model.sdk.stream.completed":
                    if ctx.get("emittedError"):
                        continue
                    events.append({
                        "day": day, "ms": ms, "model": ctx.get("modelId") or "?",
                        "src": ctx.get("querySource") or "?",
                        "think": ctx.get("reasoningDeltaChars") or 0,
                        "text": ctx.get("textDeltaChars") or 0,
                        "tools": ctx.get("toolCallCount") or 0,
                    })
    return events, calls, agent_parent


def top_session(session_id, agent_parent):
    """Owning top-level session of a record, following nested subagents up."""
    seen = set()
    while session_id and session_id.startswith(SUBAGENT_PREFIX):
        session_id = agent_parent.get(session_id[len(SUBAGENT_PREFIX):])
        if session_id in seen:
            return None
        seen.add(session_id)
    return session_id


def log_tables(events, calls, out):
    groups = collections.defaultdict(list)
    for e in events:
        groups[(e["model"], e["src"])].append(e)
    by_model = []
    for (model, src), g in sorted(groups.items()):
        think = [e["think"] for e in g]
        text = [e["text"] for e in g]
        by_model.append({
            "model": model, "querySource": src, "responses": len(g),
            "think_median": int(median(think)), "think_p90": int(quantile(think, 0.9)),
            "think_max": max(think), "think_mean": round(sum(think) / len(think)),
            "think_zero_pct": round(100 * sum(1 for t in think if t == 0) / len(think), 1),
            "text_median": int(median(text)),
            "tools_per_response": round(sum(e["tools"] for e in g) / len(g), 2),
            "think_total": sum(think),
        })
    write_csv(os.path.join(out, "by_model_role.csv"), by_model)

    dgroups = collections.defaultdict(list)
    for e in events:
        dgroups[(e["day"], e["model"], e["src"])].append(e)
    by_day = []
    for (day, model, src), g in sorted(dgroups.items()):
        think = [e["think"] for e in g]
        by_day.append({"day": day, "model": model, "querySource": src, "responses": len(g),
                       "think_median": int(median(think)), "think_p90": int(quantile(think, 0.9)),
                       "think_max": max(think)})
    write_csv(os.path.join(out, "by_day.csv"), by_day)

    per_day_agent = collections.Counter()
    for c in calls:
        per_day_agent[(c["day"], c["at"])] += 1
    tool_calls = [{"day": d, "agentType": a, "tool_calls": k} for (d, a), k in sorted(per_day_agent.items())]
    write_csv(os.path.join(out, "tool_calls.csv"), tool_calls)

    def pick(model, src):
        return [e["think"] for e in groups.get((model, src), [])]

    main54 = pick(STRONG, "main_turn")
    flash = pick(CHEAP, "subagent")
    return {
        "responses_total": len(events),
        "days": sorted({e["day"] for e in events}),
        "tool_calls_total": len(calls),
        "leader_glm54_main": {
            "responses": len(main54), "think_median": int(median(main54)),
            "think_p90": int(quantile(main54, 0.9)), "think_max": max(main54 or [0]),
        },
        "leader_glm54_as_subagent": {
            "responses": len(pick(STRONG, "subagent")),
            "think_median": int(median(pick(STRONG, "subagent"))),
        },
        "coder_glm53flash_subagent": {
            "responses": len(flash), "think_median": int(median(flash)),
            "think_p90": int(quantile(flash, 0.9)), "think_max": max(flash or [0]),
        },
        "tool_calls_by_day": {
            d: {a: k for (dd, a), k in sorted(per_day_agent.items()) if dd == d}
            for d in sorted({d for d, _ in per_day_agent})
        },
    }


def split_table(events, split_ms):
    """The coder's reasoning volume before and after a moment in time."""
    out = {}
    for name, earlier in (("before", True), ("after", False)):
        think = [e["think"] for e in events
                 if e["model"] == CHEAP and e["src"] == "subagent" and e["ms"] is not None
                 and (e["ms"] <= split_ms) == earlier]
        out[name] = {"responses": len(think), "think_median": int(median(think)),
                     "think_p90": int(quantile(think, 0.9)),
                     "responses_over_50k_chars": sum(1 for t in think if t > 50000)}
    return out


# ---------------------------------------------------------------- usage database

def read_usage(db_path, until_ms=None):
    con = open_read_only(db_path)
    try:
        sql = ("select session_id, query_source, model_id, input_tokens, output_tokens, reasoning_tokens, "
               "cache_creation_input_tokens, cache_read_input_tokens, started_at from model_usage "
               "where query_source in ('main_turn','subagent') "
               "and coalesce(input_tokens,0) + coalesce(output_tokens,0) > 0")
        params = ()
        if until_ms is not None:
            sql += " and started_at <= ?"
            params = (until_ms,)
        cur = con.execute(sql, params)
        keys = ("sess", "src", "model", "input", "output", "reasoning", "cache_creation", "cache_read", "started")
        return [dict(zip(keys, row)) for row in cur]
    finally:
        con.close()


def window_tokens(db_path, start, end, out):
    """Real tokens per model for requests started inside a local-time window."""
    con = open_read_only(db_path)
    try:
        cur = con.execute(
            "select model_id, count(*), sum(input_tokens), sum(cache_read_input_tokens), sum(output_tokens) "
            "from model_usage where started_at between ? and ? "
            "and query_source in ('main_turn','subagent') group by model_id",
            (local_ms(start), local_ms(end)))
        rows = [{"window": "%s..%s" % (start, end), "model": m, "requests": c,
                 "input_tokens": i or 0, "cache_read_tokens": cr or 0, "output_tokens": o or 0}
                for m, c, i, cr, o in cur]
    finally:
        con.close()
    write_csv(os.path.join(out, "window_tokens.csv"), rows)
    return rows


# ---------------------------------------------------------------- compare

def compare(calls, agent_parent, usage_db, specs, out, until_ms=None):
    usage = read_usage(usage_db, until_ms)

    known = {c["sess"] for c in calls}
    known |= {top_session(u["sess"], agent_parent) for u in usage}
    known.discard(None)

    def resolve(token):
        if not token.startswith("sess_"):
            token = "sess_" + token
        if token in known:
            return token
        hits = sorted(s for s in known if s.startswith(token))
        if len(hits) != 1:
            raise SystemExit("session id %r is %s; give a %s prefix" % (
                token, "unknown" if not hits else "ambiguous",
                "valid" if not hits else "longer"))
        return hits[0]

    labels = {}
    for spec in specs:
        name, _, token = spec.partition("=")
        group, _, label = name.partition("/")
        labels[resolve(token.strip())] = (group or "default", label or name)

    edits = {sid: collections.Counter() for sid in labels}
    span = {sid: [None, None] for sid in labels}
    coder_agents = collections.defaultdict(set)
    coder_edits = collections.Counter()

    def touch(sid, ms):
        if ms is None:
            return
        lo, hi = span[sid]
        span[sid] = [ms if lo is None else min(lo, ms), ms if hi is None else max(hi, ms)]

    # who made which edit: from the logs' tool calls
    for c in calls:
        if c["aid"]:
            sid = top_session(c["ps"] or agent_parent.get(c["aid"]) or "", agent_parent)
        else:
            sid = c["sess"]
        if sid not in labels:
            continue
        touch(sid, c["ms"])
        if c["aid"] and c["at"] == "coder":
            coder_agents[sid].add(c["aid"])
        if c["tool"] in EDIT_TOOLS:
            who = "main" if not c["aid"] else ("coder" if c["at"] == "coder" else "other")
            edits[sid][who] += 1
            if who == "coder":
                coder_edits[(sid, c["aid"])] += 1

    # what it cost: from the usage database
    tokens = collections.defaultdict(lambda: collections.defaultdict(new_bucket))
    main_models = collections.defaultdict(collections.Counter)
    main_ctx = collections.defaultdict(list)
    coder_rows = collections.defaultdict(list)
    unmapped = {"rows": 0, "input_tokens": 0, "output_tokens": 0}
    for u in usage:
        sid = top_session(u["sess"], agent_parent)
        if sid not in labels:
            # GLM-5.3-Flash only ever runs as the coder and only since the logs
            # begin; an unmapped row there is a coder with no tool call on record.
            if sid is None and u["model"] == CHEAP and u["sess"].startswith(SUBAGENT_PREFIX):
                unmapped["rows"] += 1
                unmapped["input_tokens"] += n(u["input"])
                unmapped["output_tokens"] += n(u["output"])
            continue
        if u["started"]:
            touch(sid, u["started"])
        add(tokens[sid][u["model"]], u)
        if not u["sess"].startswith(SUBAGENT_PREFIX):
            if u["src"] == "main_turn":
                main_models[sid][u["model"]] += 1
                main_ctx[sid].append((n(u["started"]), n(u["input"])))
        elif u["sess"][len(SUBAGENT_PREFIX):] in coder_agents[sid]:
            coder_rows[(sid, u["sess"][len(SUBAGENT_PREFIX):])].append(u)

    wide, long_rows, dispatch = [], [], []
    for sid, (group, label) in labels.items():
        total_edits = sum(edits[sid].values())
        row = {
            "group": group, "label": label,
            "start": local_stamp(span[sid][0]) if span[sid][0] is not None else "",
            "hours": round((span[sid][1] - span[sid][0]) / 3.6e6, 1) if span[sid][0] is not None else "",
            "main_models": "+".join("%s x%d" % kv for kv in main_models[sid].most_common()),
            "edits": total_edits,
            "main_edit_pct": round(100 * edits[sid]["main"] / total_edits, 1) if total_edits else "",
            "coder_edit_pct": round(100 * edits[sid]["coder"] / total_edits, 1) if total_edits else "",
            "other_edit_pct": round(100 * edits[sid]["other"] / total_edits, 1) if total_edits else "",
            "coder_subagents": len(coder_agents[sid]),
            "strong_requests_per_edit": round(tokens[sid].get(STRONG, EMPTY)["requests"] / total_edits, 2)
            if total_edits else "",
        }
        ctx = [i for _t, i in sorted(main_ctx[sid])]
        row["main_ctx_median"] = int(median(ctx)) if ctx else ""
        row["main_ctx_last"] = ctx[-1] if ctx else ""
        for alias, model in MODEL_ALIASES:
            for k in BUCKET_KEYS:
                row["%s_%s" % (alias, k)] = tokens[sid].get(model, EMPTY)[k]
        other = new_bucket()
        for model, b in tokens[sid].items():
            if not b["requests"]:
                continue
            long_rows.append({"group": group, "label": label, "model": model, **b})
            if model not in RATE:
                for k in BUCKET_KEYS:
                    other[k] += b[k]
        for k in BUCKET_KEYS:
            row["other_%s" % k] = other[k]
        wide.append(row)

        for (csid, aid), rows in coder_rows.items():
            if csid != sid:
                continue
            rows.sort(key=lambda u: n(u["started"]))
            dispatch.append({
                "group": group, "label": label, "requests": len(rows),
                "first_request_input_tokens": n(rows[0]["input"]),
                "input_tokens": sum(n(r["input"]) for r in rows),
                "cache_read_tokens": sum(n(r["cache_read"]) for r in rows),
                "output_tokens": sum(n(r["output"]) for r in rows),
                "edits": coder_edits.get((sid, aid), 0),
            })
    wide.sort(key=lambda r: r["start"])
    write_csv(os.path.join(out, "compare_sessions.csv"), wide)
    write_csv(os.path.join(out, "compare_tokens_long.csv"), long_rows)
    write_csv(os.path.join(out, "dispatch_overhead.csv"), dispatch)

    # --- pooled per group ---
    pooled, scenarios = [], []
    for group in sorted({g for g, _ in labels.values()}):
        sids = [s for s, (g, _) in labels.items() if g == group]
        E = sum(sum(edits[s].values()) for s in sids)
        by_model = collections.defaultdict(new_bucket)
        for s in sids:
            for model, b in tokens[s].items():
                if not b["requests"]:
                    continue
                for k in BUCKET_KEYS:
                    by_model[model][k] += b[k]
        for model, b in sorted(by_model.items(), key=lambda kv: -kv[1]["input_tokens"]):
            pooled.append({"group": group, "sessions": len(sids), "edits": E, "model": model,
                           "quota_rate": RATE.get(model, "unknown"),
                           "requests_per_edit": round(b["requests"] / E, 2) if E else "",
                           "per_edit_input_tokens": round(b["input_tokens"] / E) if E else "",
                           "per_edit_output_tokens": round(b["output_tokens"] / E) if E else "", **b})
        for scenario in SCENARIOS:
            total_units, any_known = 0.0, False
            for model, b in sorted(by_model.items(), key=lambda kv: -kv[1]["input_tokens"]):
                units = quota_units(b, RATE.get(model), scenario)
                if units is None:
                    continue
                any_known = True
                total_units += units
                scenarios.append({"group": group, "scenario": scenario, "model": model,
                                  "quota_units_per_edit": round(units / E) if E else ""})
            scenarios.append({"group": group, "scenario": scenario, "model": "TOTAL",
                              "quota_units_per_edit": round(total_units / E) if E and any_known else ""})
    write_csv(os.path.join(out, "compare_groups.csv"), pooled)
    write_csv(os.path.join(out, "compare_scenarios.csv"), scenarios)

    def dist(key):
        v = [d[key] for d in dispatch]
        return {"p25": int(quantile(v, 0.25)), "median": int(median(v)), "p75": int(quantile(v, 0.75))}

    overhead = {
        "coder_subagents": len(dispatch),
        "requests_per_coder": dist("requests"),
        "first_request_input_tokens": dist("first_request_input_tokens"),
        "input_tokens_per_coder": dist("input_tokens"),
        "output_tokens_per_coder": dist("output_tokens"),
        "edits_per_coder": dist("edits"),
        "share_of_coders_with_zero_edits_pct": round(
            100 * sum(1 for d in dispatch if d["edits"] == 0) / len(dispatch), 1) if dispatch else "",
    }
    return wide, overhead, unmapped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--usage-db")
    ap.add_argument("--compare", nargs="+", metavar="GROUP/LABEL=SESSION_ID")
    ap.add_argument("--until", metavar="YYYY-MM-DDTHH:MM")
    ap.add_argument("--split", metavar="YYYY-MM-DDTHH:MM")
    ap.add_argument("--window", nargs=2, metavar=("START", "END"))
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    until = local_ms(args.until) if args.until else None
    events, calls, agent_parent = load_logs(args.log_dir, until)
    summary = log_tables(events, calls, args.out_dir)
    if args.until:
        summary["until_local"] = args.until
    if args.split:
        summary["coder_reasoning_split_at"] = args.split
        summary["coder_reasoning_split"] = split_table(events, local_ms(args.split))
    if args.compare:
        if not args.usage_db:
            raise SystemExit("--compare needs --usage-db")
        wide, overhead, unmapped = compare(calls, agent_parent, args.usage_db, args.compare, args.out_dir, until)
        summary["compared_sessions"] = [[r["group"], r["label"], r["edits"]] for r in wide]
        summary["dispatch_overhead"] = overhead
        summary["coder_usage_without_a_parent_session_in_the_logs"] = unmapped
    if args.window:
        if not args.usage_db:
            raise SystemExit("--window needs --usage-db")
        summary["window_tokens"] = window_tokens(args.usage_db, args.window[0], args.window[1], args.out_dir)
    dump_json(os.path.join(args.out_dir, "summary.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
