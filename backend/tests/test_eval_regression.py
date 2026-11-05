"""Regression guard (build step 6): the full offline evaluation must not get worse than the stored reference, and must pass every safety gate.

Runs the dev set, the held-out set, the baseline comparison and the security suite through the real graph on the seeded SQLite database
(with the second-tenant fixture, so the cross-tenant cases touch real other-tenant rows). Deterministic with the offline fakes.
If this fails after a deliberate behaviour change, read evals/reports/latest.md style output (`make eval`), decide whether the new numbers are
acceptable, and only then refresh the reference (`make eval ARGS=--save-reference`). Never loosen the gates to make it pass.
"""

import json

from app.evals import metrics
from app.evals.run import DATASETS_DIR, run_all
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401

REFERENCE = DATASETS_DIR.parent / "reports" / "reference_fake_v1.json"


def test_offline_evaluation_passes_all_gates_and_does_not_regress(ingested):  # noqa: F811
    ref = json.loads(REFERENCE.read_text())
    rep = run_all(settings=settings(), reference=ref, tolerance=0.005)
    assert rep["gate_failures"] == [], rep["gate_failures"]
    assert rep["security"]["cross_tenant_fixture"] and rep["security"]["pass_rate"] == 1.0
    for name in ("dev", "heldout"):
        g, b = rep["datasets"][name]["systems"]["graph"], rep["datasets"][name]["systems"]["baseline"]
        assert g["handled_correctly_rate"] > b["handled_correctly_rate"]               # the multi-agent system must beat the single RAG bot
        assert g["human"]["escalation_recall"] >= 0.99 and b["human"]["escalation_recall"] in (0.0, None)
        assert not metrics.hard_gate_failures(g)
    assert rep["calibration"]["trusted_for_groundedness"]
