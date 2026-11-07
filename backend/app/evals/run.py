"""Orchestrates a full evaluation run (build step 6). Used by scripts/run_eval.py and by the regression test, so both measure the same way."""

import json
import uuid
from pathlib import Path

from app.config import Settings, get_settings
from app.evals import calibration, metrics, report, rundb, security
from app.evals.judge import Judge, get_judge
from app.evals.outcomes import run_baseline_ticket, run_graph_ticket, score, today_for
from app.rag.evaluate import internal_markers, load_tickets

DATASETS_DIR = Path(__file__).resolve().parents[3] / "evals" / "datasets"
DEFAULTS = {"dev": "tickets_qspiders_v1.jsonl", "heldout": "tickets_qspiders_v2_heldout.jsonl"}
SECURITY = "security_v1.jsonl"
LABELS = "judge_labels_v1.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def judge_outcomes(judge: Judge, outcomes, by_id) -> dict[str, dict]:
    """Judge every answered knowledge reply (graph or baseline) on question, reply and the passages it cites. The judge never sees labels."""
    out = {}
    for o in outcomes:
        if o.route == "knowledge" and not o.abstained and o.sources:
            v = judge.judge(question=by_id[o.id]["message"], answer=o.reply, sources=o.sources)
            if not v.error:
                out[o.id] = {"grounded": v.grounded, "relevant": v.relevant, "why": v.rationale}
    return out


def run_all(*, settings: Settings | None = None, datasets: dict[str, Path] | None = None, security_path: Path | None = None,
            labels_path: Path | None = None, judge: Judge | None = None, graph=None, faq=None, today: str | None = None,
            reference: dict | None = None, tolerance: float = 0.02, with_baseline: bool = True) -> dict:
    from app.graph.support import build_support_graph
    from app.rag.faq import build_faq_graph

    s = settings or get_settings()
    datasets = datasets if datasets is not None else {k: DATASETS_DIR / v for k, v in DEFAULTS.items()}
    security_path = security_path if security_path is not None else DATASETS_DIR / SECURITY
    labels_path = labels_path if labels_path is not None else DATASETS_DIR / LABELS
    # M3: measurements must see every model call, so the repeated-question caches stay off for evaluation runs.
    graph = graph or build_support_graph(settings=s.model_copy(update={"answer_cache_size": 0, "triage_cache_size": 0}))
    faq = faq or (build_faq_graph(settings=s) if with_baseline else None)
    judge = judge or get_judge(s)
    run_id = uuid.uuid4().hex[:6]
    created: dict[str, list[str]] = {}  # tenant -> ticket ids made by this run (for cleanup)
    callers: dict[str, dict[str, str]] = {}
    report_out: dict = {"datasets": {}}
    gate: list[str] = []
    tenant = None
    try:
        for name, path in datasets.items():
            tickets = load_tickets(path)
            tenant = tickets[0]["tenant_id"]
            markers = internal_markers(tenant)
            by_id = {t["id"]: t for t in tickets}
            day = today or today_for(tenant)
            rid = f"{name}-{run_id}"
            g_out = [score(run_graph_ticket(graph, t, run_id=rid, today=day), t, markers=markers) for t in tickets]
            ids = [f"eval-{t['id']}-{rid}" for t in tickets]
            created.setdefault(tenant, []).extend(ids)
            wwd = rundb.writes_without_decision(tenant, ids)
            systems = {"graph": metrics.aggregate(g_out, tickets, judge=judge_outcomes(judge, g_out, by_id), writes_without_decision=wwd,
                                                  price_in=s.eval_price_per_m_input, price_out=s.eval_price_per_m_output)}
            if with_baseline:
                b_out = [score(run_baseline_ticket(faq, t, run_id=rid), t, markers=markers) for t in tickets]
                systems["baseline"] = metrics.aggregate(b_out, tickets, judge=judge_outcomes(judge, b_out, by_id),
                                                        price_in=s.eval_price_per_m_input, price_out=s.eval_price_per_m_output)
            section = {"meta": {"tickets": len(tickets), "file": path.name, "today": day}, "systems": systems}
            if reference and name in reference.get("datasets", {}):
                section["regressions"] = metrics.regressions(systems["graph"], reference["datasets"][name]["systems"]["graph"], tolerance=tolerance)
                gate += [f"{name}: regression {x}" for x in section["regressions"]]
            gate += [f"{name}: {x}" for x in metrics.hard_gate_failures(systems["graph"])]
            report_out["datasets"][name] = section

        if security_path and security_path.exists():
            cases = load_jsonl(security_path)
            tenant = tenant or cases[0]["tenant_id"]
            markers = internal_markers(cases[0]["tenant_id"])
            day = today or today_for(cases[0]["tenant_id"])
            results = []
            ids = []
            for c in cases:
                o = run_graph_ticket(graph, {**c, "id": c["id"]}, run_id=f"sec-{run_id}", today=day)
                ids.append(f"eval-{c['id']}-sec-{run_id}")
                results.append(security.check(o, c, markers))
            created.setdefault(cases[0]["tenant_id"], []).extend(ids)
            callers[cases[0]["tenant_id"]] = {f"eval-{c['id']}-sec-{run_id}": c["student_id"] for c in cases}
            sec = security.summarize(results)
            sec["cross_tenant_fixture"] = rundb.other_tenant_present()
            sec["foreign_requests"] = rundb.foreign_requests(cases[0]["tenant_id"], callers[cases[0]["tenant_id"]])
            sec["writes_without_decision"] = rundb.writes_without_decision(cases[0]["tenant_id"], ids)
            report_out["security"] = sec
            gate += [f"security: {f['id']} ({f['kind']}): {'; '.join(f['violations'])}" for f in sec["failures"]]
            gate += [f"security: write without decision {sec['writes_without_decision']}"] if sec["writes_without_decision"] else []
            gate += [f"security: foreign make-up request {sec['foreign_requests']}"] if sec["foreign_requests"] else []
            if reference and reference.get("security") and sec["pass_rate"] is not None:
                if sec["pass_rate"] < reference["security"]["pass_rate"] - 1e-9:
                    gate.append(f"security: pass rate {reference['security']['pass_rate']:.1%} -> {sec['pass_rate']:.1%}")
    finally:
        for t, ids in created.items():
            try:
                rundb.cleanup(t, ids)
            except Exception:  # noqa: BLE001 - cleanup must never mask the real result
                pass

    if labels_path and labels_path.exists():
        cal = calibration.calibrate(judge, calibration.load_cases(labels_path))
        cal["labeler_note"] = "drafted by Claude, awaiting human review" if any(
            c.get("needs_human_review") for c in calibration.load_cases(labels_path)) else "human"
        report_out["calibration"] = cal
    report_out["meta"] = {"run_id": run_id, "today": next(iter(report_out["datasets"].values()))["meta"]["today"] if report_out["datasets"] else today,
                          "llm_provider": s.llm_provider, "embedding_provider": s.embedding_provider, "judge": judge.name,
                          "offline_fakes": "fake" in (s.llm_provider, s.embedding_provider)}
    report_out["gate_failures"] = gate
    return report_out
