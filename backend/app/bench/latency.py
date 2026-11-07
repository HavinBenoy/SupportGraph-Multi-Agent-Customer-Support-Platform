"""Pure helpers for the M3 latency benchmark (statistics, grouping, rendering, before/after comparison). No I/O, no model calls: unit-tested.

A "run" is one question through the whole support graph:
  {"scenario", "i", "wall_ms", "route", "status", "abstained", "perf": {...from observability/stages.py...}, "node_ms": {...}, "cpu_busy_pct", "mem_available_mb"}
Metrics are flattened to dotted names: wall_ms, graph_ms, unaccounted_ms, node.<name>, stage.<name>, llm.<metric>, cpu_busy_pct.
Groups: all, warm (not cold), cold (the run loaded a model or was a first run without warm-up), cache_hit (answer or triage cache served part of it).
Percentiles are only printed when there are enough samples to mean something: P90 needs >= 10 runs, P95 needs >= 20 (otherwise "-").
"""

from app.bench.stats import percentile

MIN_N_P90, MIN_N_P95 = 10, 20


def stat_block(values: list[float]) -> dict:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0}
    n = len(vals)
    return {"n": n, "mean": round(sum(vals) / n, 1), "median": round(percentile(vals, 50), 1),
            "p90": round(percentile(vals, 90), 1) if n >= MIN_N_P90 else None,
            "p95": round(percentile(vals, 95), 1) if n >= MIN_N_P95 else None,
            "min": round(min(vals), 1), "max": round(max(vals), 1)}


def flatten(run: dict) -> dict[str, float]:
    perf = run.get("perf") or {}
    m: dict[str, float] = {"wall_ms": run.get("wall_ms")}
    if perf:
        m["graph_ms"], m["unaccounted_ms"] = perf.get("graph_ms"), perf.get("unaccounted_ms")
    for k, v in (run.get("node_ms") or {}).items():
        m[f"node.{k}"] = v
    for k, v in (perf.get("stages") or {}).items():
        m[f"stage.{k}"] = v
    for node, st in (perf.get("by_node") or {}).items():
        if "other" in st:
            m[f"other.{node}"] = st["other"]
    calls = perf.get("llm") or []
    if calls:
        m["llm.calls"] = len(calls)
        for key in ("load_ms", "prompt_tokens", "prompt_eval_ms", "decode_tokens", "decode_ms"):
            m[f"llm.{key}"] = round(sum(c.get(key) or 0 for c in calls), 1)
    for k in ("cpu_busy_pct", "mem_available_mb"):
        if run.get(k) is not None:
            m[k] = run[k]
    return {k: v for k, v in m.items() if v is not None}


def groups_of(runs: list[dict]) -> dict[str, list[dict]]:
    out = {"all": runs, "warm": [r for r in runs if not (r.get("perf") or {}).get("cold")],
           "cold": [r for r in runs if (r.get("perf") or {}).get("cold")]}
    hits = [r for r in runs if any(v == "hit" for k, v in ((r.get("perf") or {}).get("flags") or {}).items() if k in ("answer_cache", "triage_cache"))]
    if hits:
        out["cache_hit"] = hits
    return {k: v for k, v in out.items() if v}


def summarise_runs(runs: list[dict]) -> dict:
    summary: dict = {}
    for name, rs in groups_of(runs).items():
        names = sorted({k for r in rs for k in flatten(r)})
        summary[name] = {k: stat_block([flatten(r).get(k) for r in rs]) for k in names}
    return summary


_KEY = ("wall_ms", "graph_ms", "node.triage", "node.knowledge", "stage.triage_llm", "stage.cache_check", "stage.embed", "stage.search",
        "stage.context", "stage.llm", "stage.llm_retry", "stage.validate", "other.knowledge", "unaccounted_ms", "llm.load_ms", "llm.prompt_tokens",
        "llm.decode_tokens", "cpu_busy_pct")


def _cell(v) -> str:
    return "-" if v is None else f"{v:g}"


def render(report: dict) -> str:
    meta = report.get("meta", {})
    lines = [f"M3 latency benchmark  label={meta.get('label', '-')}  when={meta.get('when', '-')}"]
    if meta.get("fake"):
        lines += ["!! MODELS: FAKE (offline stand-ins). These numbers check the plumbing only. They are NOT model performance. !!"]
    lines.append(f"settings: {meta.get('settings', {})}")
    for name, sc in report.get("scenarios", {}).items():
        lines += ["", f"== {name}  ({len(sc['runs'])} runs)"]
        for gname, g in sc["summary"].items():
            lines.append(f"  [{gname}]  metric                    n    mean  median    p90    p95     min     max")
            for k in _KEY:
                if k in g and g[k].get("n"):
                    b = g[k]
                    lines.append(f"    {k:<26}{b['n']:>4}{_cell(b['mean']):>8}{_cell(b['median']):>8}{_cell(b['p90']):>7}{_cell(b['p95']):>7}{_cell(b['min']):>8}{_cell(b['max']):>8}")
    return "\n".join(lines)


def compare(before: dict, after: dict, group: str = "warm", metrics: tuple[str, ...] = _KEY) -> str:
    """Before/after table of MEDIANS per scenario. Only scenarios and metrics present in both files are compared. Run both with the same
    command line, same machine and the same documents (the settings snapshot of each file is shown so a mismatch is visible)."""
    lines = [f"before: label={before.get('meta', {}).get('label')}  settings={before.get('meta', {}).get('settings')}",
             f"after : label={after.get('meta', {}).get('label')}  settings={after.get('meta', {}).get('settings')}",
             f"group: {group}   (median ms; negative change = faster)"]
    for name in sorted(set(before.get("scenarios", {})) & set(after.get("scenarios", {}))):
        b = before["scenarios"][name]["summary"].get(group, {})
        a = after["scenarios"][name]["summary"].get(group, {})
        rows = []
        for k in metrics:
            if k in b and k in a and b[k].get("n") and a[k].get("n") and b[k]["median"]:
                d = a[k]["median"] - b[k]["median"]
                rows.append(f"    {k:<26}{b[k]['median']:>9g}{a[k]['median']:>9g}{d:>+9.1f}{100 * d / b[k]['median']:>+8.1f}%")
        if rows:
            lines += ["", f"== {name}", f"    {'metric':<26}{'before':>9}{'after':>9}{'delta':>9}{'change':>9}", *rows]
    return "\n".join(lines)
