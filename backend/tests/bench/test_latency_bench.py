"""The M3 benchmark's statistics and rendering (pure functions). The runner script needs real services; its logic is exercised here without them."""

import json
import subprocess
import sys
from pathlib import Path

from app.bench import latency as L


def run(i, wall, cold=False, **perf):
    return {"scenario": "s", "i": i, "wall_ms": wall, "node_ms": {"triage": 10, "knowledge": wall - 20},
            "perf": {"graph_ms": wall, "unaccounted_ms": 3, "cold": cold, "stages": {"embed": 5.0, "llm": wall - 40.0}, "by_node": {"knowledge": {"other": 2.0}},
                     "llm": [{"load_ms": 4000.0 if cold else 20.0, "prompt_tokens": 100, "prompt_eval_ms": 50.0, "decode_tokens": 20, "decode_ms": 900.0}], **perf},
            "cpu_busy_pct": 55.0}


def test_percentiles_appear_only_when_there_are_enough_samples():
    few = L.stat_block([100, 200, 300, 400, 500])
    assert few["n"] == 5 and few["median"] == 300 and few["mean"] == 300 and few["p90"] is None and few["p95"] is None
    ten = L.stat_block(list(range(1, 11)))
    assert ten["p90"] is not None and ten["p95"] is None
    twenty = L.stat_block(list(range(1, 21)))
    assert twenty["p90"] is not None and twenty["p95"] is not None and twenty["p95"] > twenty["p90"]
    assert L.stat_block([]) == {"n": 0} and L.stat_block([None, 5])["n"] == 1


def test_cold_and_warm_runs_are_never_averaged_together():
    runs = [run(0, 11000, cold=True), run(1, 6000), run(2, 6200), run(3, 5900)]
    g = L.summarise_runs(runs)
    assert g["cold"]["wall_ms"]["n"] == 1 and g["cold"]["wall_ms"]["median"] == 11000
    assert g["warm"]["wall_ms"]["n"] == 3 and g["warm"]["wall_ms"]["median"] == 6000 and g["all"]["wall_ms"]["n"] == 4
    assert g["cold"]["llm.load_ms"]["median"] == 4000 and g["warm"]["llm.load_ms"]["median"] == 20


def test_flatten_names_every_stage_node_and_llm_counter():
    m = L.flatten(run(0, 6000))
    assert {"wall_ms", "graph_ms", "unaccounted_ms", "node.triage", "node.knowledge", "stage.embed", "stage.llm", "other.knowledge", "llm.calls",
            "llm.prompt_tokens", "llm.decode_tokens", "cpu_busy_pct"} <= set(m)


def test_cache_hits_get_their_own_group():
    hit = run(1, 50, flags={"answer_cache": "hit"})
    g = L.summarise_runs([run(0, 6000), hit])
    assert g["cache_hit"]["wall_ms"]["n"] == 1 and "cache_hit" not in L.summarise_runs([run(0, 6000)])


def report(label, walls, fake=False):
    runs = [run(i, w) for i, w in enumerate(walls)]
    return {"meta": {"label": label, "when": "t", "fake": fake, "settings": {"rag_top_k": 4}}, "scenarios": {"warm_faq_varied": {"runs": runs, "summary": L.summarise_runs(runs)}}}


def test_the_table_says_loudly_when_the_models_were_fake():
    assert "FAKE" in L.render(report("x", [10, 11], fake=True)) and "FAKE" not in L.render(report("x", [10, 11]))
    assert "warm_faq_varied" in L.render(report("x", [10, 11])) and "node.knowledge" in L.render(report("x", [10, 11]))


def test_compare_shows_signed_deltas_per_metric():
    out = L.compare(report("before", [6000, 6200, 5800]), report("after", [4500, 4600, 4400]))
    line = next(x for x in out.splitlines() if x.strip().startswith("wall_ms"))
    assert "-1500.0" in line and "-25.0%" in line and "label=before" in out and "label=after" in out


def test_the_benchmark_script_compares_two_saved_reports(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(report("before", [6000, 6100, 5900])))
    b.write_text(json.dumps(report("after", [5000, 5100, 4900])))
    script = Path(__file__).resolve().parents[2] / "scripts" / "bench_voice_latency.py"
    r = subprocess.run([sys.executable, str(script), "--compare", str(a), str(b)], capture_output=True, text=True, cwd=script.parent.parent)
    assert r.returncode == 0 and "-1000.0" in r.stdout and "wall_ms" in r.stdout, r.stderr
