"""Machine-readable (JSON) and human-readable (Markdown) evaluation reports (build step 6)."""

import json
from pathlib import Path

from app.evals.metrics import COMPARE_ROWS, dig


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def fmt(path: str, x) -> str:
    if x is None:
        return "n/a"
    return pct(x) if ("rate" in path or "accuracy" in path or "recall" in path or "precision" in path) else f"{x:g}"


def dataset_markdown(name: str, meta: dict, section: dict) -> str:
    systems = section["systems"]
    g, b = systems["graph"], systems.get("baseline")
    L = [f"## Dataset `{name}`: {meta['tickets']} tickets", ""]
    if b:
        L += ["", "## Support graph vs the step 2 single-RAG-bot baseline", "", "| Metric | Baseline | Graph |", "|---|---|---|"]
        for label, path in COMPARE_ROWS:
            L.append(f"| {label} | {fmt(path, dig(b, path))} | {fmt(path, dig(g, path))} |")
        L += ["", "By expected route (share of tickets handled correctly):", "", "| Route | n | Baseline | Graph |", "|---|---|---|---|"]
        for r, v in g["handled_by_route"].items():
            L.append(f"| {r} | {v['n']} | {pct(b['handled_by_route'].get(r, {}).get('handled_correctly'))} | {pct(v['handled_correctly'])} |")
    L += ["", "### Graph: safety (every line must be none)", ""]
    for k, v in g["safety"].items():
        if k != "wrong_action_rate":
            L.append(f"- {k}: {v or 'none'}" if k not in ("qa_blocked",) else f"- {k} (informational): {v or 'none'}")
    L += ["", "### Graph: quality", "",
          f"- route accuracy {pct(g['route_accuracy'])}; triage category {pct(g['triage'].get('category_accuracy'))} "
          f"(on {g['triage'].get('category_scored')} scored), priority {pct(g['triage'].get('priority_accuracy'))}, sentiment {pct(g['triage'].get('sentiment_accuracy'))}",
          f"- knowledge: correct {pct(g['knowledge']['correct_rate'])}, wrongly answered {pct(g['knowledge']['wrong_answer_rate'])}, "
          f"correctly declined {pct(g['knowledge']['abstain_rate'])}, over-declined {pct(g['knowledge']['over_abstain_rate'])}",
          f"- groundedness: unsupported-number rate {pct(g['groundedness']['unsupported_number_rate'])}; judge grounded "
          f"{pct(g['groundedness']['judge_grounded_rate'])} on {g['groundedness']['judge_scored']} answers",
          f"- humans: escalation precision {pct(g['human']['escalation_precision'])}, recall {pct(g['human']['escalation_recall'])}; "
          f"automation {pct(g['human']['automation_rate'])}, correct automation {pct(g['human']['correct_automation_rate'])}",
          f"- speed: p50 {g['performance']['latency_ms']['p50']} ms, p95 {g['performance']['latency_ms']['p95']} ms; "
          f"{g['performance']['llm_calls_per_ticket']} model calls and {g['performance']['tokens_per_ticket']} tokens per ticket"
          f"{' (estimated, not provider-reported)' if g['performance']['tokens_estimated'] else ''}; cost per ticket ${g['performance']['cost_per_ticket_usd']}"]
    weak = {k: v for k, v in g["slices"]["tag"].items() if v["handled_correctly"] is not None and v["handled_correctly"] < 1}
    if weak:
        L += ["", "Weak slices (tag: handled correctly):", ""] + [f"- {k}: {pct(v['handled_correctly'])} of {v['n']}" for k, v in weak.items()]
    if g["failures"]:
        L += ["", f"### Graph failures ({len(g['failures'])})", ""] + [f"- {f['id']}: {'; '.join(f['problems'])}  \n  \"{f['message'][:110]}\"" for f in g["failures"]]
    if section.get("regressions") is not None:
        L += ["", "Regression check against the stored reference:", ""] + ([f"- {r}" for r in section["regressions"]] or ["- no tracked metric got worse"])
    return "\n".join(L) + "\n"


def security_markdown(sec: dict) -> str:
    L = ["## Security suite (adversarial cases through the real graph)", "",
         f"{sec['passed']} of {sec['cases']} cases passed ({pct(sec['pass_rate'])}). Cross-tenant cases ran against live other-tenant data: "
         f"{'yes' if sec.get('cross_tenant_fixture') else 'NO (fixture absent, those cases only prove the reply held no other-tenant text)'}.", "",
         "| Kind | Cases | Passed |", "|---|---|---|"]
    L += [f"| {k} | {v['n']} | {v['passed']} |" for k, v in sec["by_kind"].items()]
    if sec["failures"]:
        L += ["", "Failures:", ""] + [f"- {f['id']} ({f['kind']}): {'; '.join(f['violations'])}  \n  reply: \"{f['reply']}\"" for f in sec["failures"]]
    return "\n".join(L) + "\n"


def calibration_markdown(c: dict) -> str:
    g, r = c["grounded"], c["relevant"]
    return "\n".join(["## Judge calibration against human labels", "",
        f"Judge `{c['judge']}` on {c['cases']} labelled answers (labels: {c.get('labeler_note', 'see file')}).", "",
        f"- groundedness: agreement {pct(g['agreement'])}, kappa {g['kappa']}, flags {g['human_negatives']} human-labelled ungrounded answers with "
        f"precision {pct(g['flag_precision'])} / recall {pct(g['flag_recall'])} ({g['missed']} hallucinations missed, {g['false_alarms']} false alarms)",
        f"- relevance: agreement {pct(r['agreement'])}, kappa {r['kappa']}, only {r['human_negatives']} irrelevant answers in the set",
        f"- trusted for groundedness: **{c['trusted_for_groundedness']}** (needs kappa >= {c['min_kappa']}); trusted for relevance: **{c['trusted_for_relevance']}**", ""]) + "\n"


def full_markdown(report: dict) -> str:
    m = report["meta"]
    L = [f"# Evaluation report (run `{m['run_id']}`)", "",
         f"- evaluated as of {m['today']}; chat model `{m['llm_provider']}`, embeddings `{m['embedding_provider']}`, judge `{m['judge']}`"]
    if m["offline_fakes"]:
        L += ["", "> **Offline fakes in use.** These numbers test the plumbing and how well keyword rules fit these ticket sets. They do not predict how "
              "a real model performs. Re-run with real providers before quoting any of them."]
    L += [""]
    for name, section in report["datasets"].items():
        L.append(dataset_markdown(name, section["meta"], section))
    if report.get("security"):
        L.append(security_markdown(report["security"]))
    if report.get("calibration"):
        L.append(calibration_markdown(report["calibration"]))
    if report.get("gate_failures"):
        L += ["## GATE FAILURES", ""] + [f"- {x}" for x in report["gate_failures"]]
    return "\n".join(L) + "\n"


def write(report: dict, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / f"{name}.json", out_dir / f"{name}.md"
    j.write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")
    m.write_text(full_markdown(report), encoding="utf-8")
    return j, m
