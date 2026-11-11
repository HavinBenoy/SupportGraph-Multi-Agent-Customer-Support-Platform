"""Multi-task change requests (final M6): decomposition, sequential application on an unpublished working copy, per-task status, the final cross-task check.
Scripted model (it edits only what ITS task asks for, like a good Qwen). Real Qwen: scripts/judge_probe.py --suite (scenarios 18-19)."""

import re

from langchain_core.messages import AIMessage

from app.knowledge.impact import tasks as TK
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.test_generalized_flow import analysed, seed
from tests.knowledge.test_pipeline_trace import events, use
from tests.rag.conftest import embedder, ingested  # noqa: F401

SIX = ("Change the Gen AI faculty to Mohanan sir, Machine Learning faculty to Gopan sir, Python faculty to Jobi sir and DSA faculty to Karthik sir. "
       "Also change Gen AI class timing to evening and German class timing to morning 9 AM.")
GENAI = "# Gen AI\n\nThe Gen AI faculty is Vinu sir and the class timing is morning.\nFees of gen ai course is 25000 rupees.\n"
ML = "# Machine Learning\n\nMachine Learning faculty is Santhosh sir.\nFee for Machine learning course is 9999 rupees.\n"
PY = "# Python\n\nPython faculty is Arun sir.\n"
DSA = "# DSA\n\nDSA faculty is Meera madam.\n"
GER = "# German\n\nGerman class timing is evening 6 PM.\n"


class PerTask:
    """Edits the section according to the task in the prompt (the 'Requested change:' line), and nothing else. `fail` = substrings of tasks that get a cut-off answer."""

    RULES = [(r"gen ai faculty to (\w+ sir)", r"Vinu sir", None), (r"machine learning faculty to (\w+ sir)", r"Santhosh sir", None),
             (r"python faculty to (\w+ sir)", r"Arun sir", None), (r"dsa faculty to (\w+ sir)", r"Meera madam", None),
             (r"gen ai class timing to (\w+)", r"timing is morning", "timing is {}"), (r"german class timing to (morning 9 am)", r"evening 6 PM", "morning 9 AM"),
             (r"fees of gen ai to (\d+)", r"25000", None), (r"fees of machine learning to (\d+)", r"9999", None)]

    def __init__(self, fail=()):
        self.fail, self.calls = fail, []

    def with_structured_output(self, _):
        return self

    def invoke(self, msgs):
        text = msgs[0].content if isinstance(msgs, list) else str(msgs)
        m = re.search(r"<section>\n(.*?)\n</section>", text, re.S)
        if not m:
            return AIMessage("{}")
        self.calls.append(text)
        req = re.search(r"Requested change: (.*)", text).group(1).lower()
        if any(f in req for f in self.fail):
            return AIMessage("```text\nhalf")
        sec = m.group(1)
        for pat, old, fmt in self.RULES:
            g = re.search(pat, req)
            if g:
                val = g.group(1)
                new = fmt.format(val) if fmt else (val.title().replace(" Sir", " sir") if "sir" in val else val)
                sec = re.sub(old, new, sec, flags=re.I)
        return AIMessage("```text\n" + sec + "\n```")


class TaskModel:
    """A chat whose structured output returns a given TaskList (the decomposition call)."""

    def __init__(self, tasks):
        self.tasks = tasks

    def with_structured_output(self, schema):
        outer = self

        class _S:
            def invoke(self, _):
                return schema.model_validate({"tasks": [{"id": i + 1, "description": t} for i, t in enumerate(outer.tasks)]})
        return _S()


# ======================================================================================================================== decomposition
def test_a_single_change_stays_one_task_and_costs_no_model_call():
    d = TK.decompose("Change the Gen AI faculty to Mohanan sir", TaskModel(["x", "y"]))
    assert d.tasks == ["Change the Gen AI faculty to Mohanan sir"] and d.source == "single"


def test_four_faculty_changes_and_two_timings_are_six_tasks():
    d = TK.decompose(SIX)
    assert d.source == "rules" and len(d.tasks) == 6 and not d.uncovered
    assert d.tasks[4].lower() == "change gen ai class timing to evening" and d.tasks[5] == "Change German class timing to morning 9 AM"


def test_a_grounded_model_task_list_is_used_and_an_invented_one_is_refused():
    good = ["Change the fees of Gen AI to 15000 rupees", "Change the fees of Machine Learning to 6000 rupees"]
    d = TK.decompose("Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.", TaskModel(good))
    assert d.source == "model" and d.tasks == good and not d.uncovered
    bad = TK.decompose("Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.", TaskModel(good + ["Change the Java fee to 3000 rupees"]))
    assert bad.source == "rules" and "not grounded" in bad.note and len(bad.tasks) == 2
    short = TK.decompose("Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.", TaskModel(good[:1]))
    assert short.source == "rules" and len(short.tasks) == 2                                            # a list that covers less is not used


def test_a_broken_model_answer_falls_back_to_the_rules_and_says_so():
    class Broken:
        def with_structured_output(self, _):
            raise RuntimeError("no json")
    d = TK.decompose(SIX, Broken())
    assert d.source == "rules" and len(d.tasks) == 6 and "could not be used" in d.note


def test_duplicates_are_merged_and_uncovered_values_are_reported():
    d = TK.decompose("Change the Python faculty to Jobi sir and change the Python faculty to Jobi sir", TaskModel(
        ["Change the Python faculty to Jobi sir", "Change the Python faculty to Jobi sir"]))
    assert len(d.tasks) == 1 and d.duplicates
    assert "9 am" in TK._coverage(["Change German class timing to morning"], "Change German class timing to morning 9 AM")


def test_the_final_check_names_a_task_whose_value_was_overwritten():
    rows = [{"intent": 1, "text": "Change Gen AI faculty to Mohanan sir", "new_values": ["Mohanan sir"], "edited": ["genai"], "attribute": "faculty", "subject_terms": ["gen", "ai"]},
            {"intent": 2, "text": "Change Gen AI faculty to Gopan sir", "new_values": ["Gopan sir"], "edited": ["genai"], "attribute": "faculty", "subject_terms": ["gen", "ai"]}]
    probs = TK.final_check(rows, {"genai": "The Gen AI faculty is Gopan sir."}, {"genai": "The Gen AI faculty is Vinu sir."})
    assert any("Mohanan sir" in p and "overwritten" in p for p in probs) and any("both change the same thing" in p for p in probs)


# ===================================================================================================================== through the application
def test_six_tasks_across_five_documents_all_land_and_nothing_is_published(api, monkeypatch):  # noqa: F811
    chat = PerTask()
    use(monkeypatch, chat)
    seed(api, genai=("Gen AI", GENAI), ml=("Machine Learning", ML), py=("Python", PY), dsa=("DSA", DSA), ger=("German", GER))
    req = analysed(api, SIX)
    items = {i["doc_id"]: i for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert set(items) == {"genai", "ml", "py", "dsa", "ger"}
    g = items["genai"]["proposed_content"]
    assert "Mohanan sir" in g and "timing is evening" in g and "Vinu" not in g                   # two tasks on ONE sentence: both changes kept
    assert "Gopan sir" in items["ml"]["proposed_content"] and "Jobi sir" in items["py"]["proposed_content"] and "Karthik sir" in items["dsa"]["proposed_content"]
    assert "morning 9 AM" in items["ger"]["proposed_content"]
    assert [t["task"] for t in items["genai"]["ai"]["tasks"]] == [1, 5]                           # task-level provenance
    assert events(req, "task", "working_copy", "genai")                                              # task 5 read task 1's revision
    tasks = req["analysis"]["intents"]
    assert len(tasks) == 6 and all(t["status"] in ("completed", "confirm") for t in tasks)
    assert "Vinu sir" in A.live_text("genai") and "Arun sir" in A.live_text("py")                    # nothing published


def test_the_fee_example_two_tasks_two_documents(api, monkeypatch):  # noqa: F811
    use(monkeypatch, PerTask())
    seed(api, genai=("Gen AI", GENAI), ml=("Machine Learning", ML))
    req = analysed(api, "Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.")
    items = {i["doc_id"]: i for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert "15000 rupees" in items["genai"]["proposed_content"] and "6000 rupees" in items["ml"]["proposed_content"]
    assert "15000" not in items["ml"]["proposed_content"] and "6000" not in items["genai"]["proposed_content"]


def test_a_failed_task_keeps_the_successful_ones_and_is_reported(api, monkeypatch):  # noqa: F811
    use(monkeypatch, PerTask(fail=("python faculty",)))
    seed(api, genai=("Gen AI", GENAI), ml=("Machine Learning", ML), py=("Python", PY), dsa=("DSA", DSA), ger=("German", GER))
    req = analysed(api, SIX)
    with_edits = {i["doc_id"] for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert {"genai", "ml", "dsa", "ger"} <= with_edits
    st = {t["intent"]: t["status"] for t in req["analysis"]["intents"]}
    assert events(req, "model_call", "truncated", "py")                                              # the failure is recorded for its task ...
    assert all(st[k] in ("completed", "confirm") for k in (1, 2, 4, 5, 6))                         # ... and every other task keeps its proposal
    py = A.item(req, "py")
    assert py is not None and (not py["ai"]["edits"] or py["classification"] in ("definite", "potential"))   # the rules may still propose the slot change; never silently gone


def test_a_task_with_no_document_is_reported_not_dropped(api, monkeypatch):  # noqa: F811
    use(monkeypatch, PerTask())
    seed(api, genai=("Gen AI", GENAI))
    req = analysed(api, "Change the Gen AI faculty to Mohanan sir and the Quantum faculty to Ravi sir")
    st = {t["intent"]: (t["status"], t["status_label"]) for t in req["analysis"]["intents"]}
    assert st[1][0] in ("completed", "confirm") and st[2] == ("no_document", "No relevant document found")


class SplittingModel(PerTask):
    """PerTask that also answers the decomposition call with a schema-validated TaskList (what qwen3:4b-instruct did in the user's local transcript)."""

    def __init__(self, tasks, **kw):
        super().__init__(**kw)
        self.tasks = tasks

    def with_structured_output(self, schema):
        if schema is TK.TaskList:
            return TaskModel(self.tasks).with_structured_output(schema)
        return self


def test_the_application_asks_the_impact_model_for_the_task_list(api, monkeypatch):  # noqa: F811
    tasks = ["Change the fees of Gen AI to 15000 rupees", "Change the fees of Machine Learning to 6000 rupees"]
    use(monkeypatch, SplittingModel(tasks))
    seed(api, genai=("Gen AI", GENAI), ml=("Machine Learning", ML))
    req = analysed(api, "Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.")
    dec = req["analysis"]["decomposition"]
    assert dec["source"] == "model" and dec["tasks"] == tasks and not dec["uncovered"]
    assert events(req, "decomposition", "model")
    items = {i["doc_id"]: i for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert "15000 rupees" in items["genai"]["proposed_content"] and "6000 rupees" in items["ml"]["proposed_content"]


def test_an_invented_model_task_is_refused_in_the_application_too(api, monkeypatch):  # noqa: F811
    use(monkeypatch, SplittingModel(["Change the fees of Gen AI to 15000 rupees", "Change the Java fee to 3000 rupees"]))
    seed(api, genai=("Gen AI", GENAI), ml=("Machine Learning", ML))
    req = analysed(api, "Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees.")
    dec = req["analysis"]["decomposition"]
    assert dec["source"] == "rules" and "not grounded" in dec["note"] and len(dec["tasks"]) == 2


def test_an_old_and_a_new_value_are_one_change_and_cost_no_model_call():
    assert not TK.looks_multi("change the work experience of santhosh sir from 3 years to 5 years")
    assert TK.looks_multi("change gen ai fee to 15000 ml fee to 6000")
    assert TK.decompose("change the work experience of santhosh sir from 3 years to 5 years", TaskModel(["x", "y"])).source == "single"
