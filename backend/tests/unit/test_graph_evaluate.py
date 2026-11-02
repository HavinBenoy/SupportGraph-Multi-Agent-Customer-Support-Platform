"""The routing evaluator's arithmetic, on stubbed results (no graph needed)."""

from app.graph.evaluate import RoutedResult, run_routing_eval


def t(id, route, **kw):
    base = {"id": id, "expected_route": route, "needs_human": route == "escalate", "student_id": "stu_asha",
            "expected_tools": [], "expected_facts": [], "must_not_contain": []}
    return {**base, **kw}


def test_metrics_and_problem_lists():
    tickets = [
        t("a", "knowledge"), t("b", "escalate"), t("c", "escalate"), t("d", "action", expected_tools=["get_my_attendance"], expected_facts=["80"]),
        t("e", "knowledge", must_not_contain=["SECRET"]), t("f", "clarify", student_id="anonymous"),
    ]
    results = {
        "a": RoutedResult("knowledge"),
        "b": RoutedResult("escalate", needs_human=True),
        "c": RoutedResult("knowledge"),                                   # missed escalation
        "d": RoutedResult("action", reply="Attendance is 80%", tools_called=["get_my_attendance"]),
        "e": RoutedResult("knowledge", reply="the SECRET is x"),          # leak
        "f": RoutedResult("action", tools_called=["get_my_batches"]),     # prospect reached a tool
    }
    m = run_routing_eval(lambda x: results[x["id"]], tickets)
    assert abs(m["route_accuracy"] - 4 / 6) < 1e-3
    assert m["escalation_precision"] == 1.0 and m["escalation_recall"] == 0.5
    assert m["tool_set_match"] == 1.0 and m["action_facts_rate"] == 1.0
    assert m["leaks"] == ["e"] and m["unauthorized_tool_calls"] == ["f"] and m["unexpected_tool_calls"] == ["f"]
    assert [w["id"] for w in m["wrong_route"]] == ["c", "f"]


def test_internal_markers_count_as_leaks():
    m = run_routing_eval(lambda x: RoutedResult("knowledge", reply="Per the INTERNAL rule 7"), [t("a", "knowledge")], markers=["internal rule 7"])
    assert m["leaks"] == ["a"]
