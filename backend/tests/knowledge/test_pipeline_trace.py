"""The real-model failure reports (Santhosh sir: remove education, change the past industry, correct several statements) through the REAL application, with a scripted model
that answers the way Qwen did in the user's own Ollama session, plus the per-request processing trace.

Scripted model, real everything else (HTTP routes, analysis job, discovery, context, validation, persistence). NOT a real Qwen: see scripts/judge_probe.py --suite for that."""

import logging
import re

from langchain_core.messages import AIMessage

from app.knowledge.impact import judge as J
from app.knowledge.impact.evaluate import LlmImpactModel
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.test_generalized_flow import analysed, release, seed
from tests.rag.conftest import embedder, ingested  # noqa: F401

LINE = "Machine Learning faculty is Santhosh sir. He has a work experience of 3 years. Previously he worked in IT. He do have a M.Tech in Data Science."
DS = ("# Data Science\n\n## Course details\n\nThe course covers Python, statistics and machine learning.\n\n## Faculty\n\n" + LINE
      + "\n\n## Fees\n\n| Course | Fee |\n|---|---|\n| Data Science | 15000 |\n")
WEB = "# Web Development\n\n## Faculty\n\nWeb faculty is Rao sir. He has a work experience of 7 years.\n"
HIST = "# Data Science\n\n## Fees\n\nIn 2021 the fee was 10000.\nThe fee is 10000.\n"


class Model:
    """Scripted chat: `answers` is a list of callables (section -> text) used in turn (the last one repeats); `error` raises; `thinks=False` refuses thinking like an instruct build."""

    def __init__(self, *answers, error=None, thinks=True, reasoning=True):
        self.answers, self.error, self.thinks, self.reasoning, self.calls = list(answers), error, thinks, reasoning, []

    def with_structured_output(self, _):
        return self

    def model_copy(self, update=None):
        m = Model(*self.answers, error=self.error, thinks=self.thinks, reasoning=(update or {}).get("reasoning", self.reasoning))
        m.calls = self.calls
        return m

    def invoke(self, msgs):
        text = msgs[0].content
        m = re.search(r"<section>\n(.*?)\n</section>", text, re.S)
        if not m:
            return AIMessage("{}")                                          # the structured parse call: unusable, the rules / general path decide
        if self.reasoning and not self.thinks:
            raise RuntimeError('registry.ollama.ai/library/qwen3:4b-instruct-2507 does not support thinking (status code: 400)')
        self.calls.append(text)
        if self.error:
            raise self.error
        f = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        return AIMessage("ok\n```text\n" + f(m.group(1)) + "\n```")


def sub(*pairs):
    def f(sec):
        for a, b in pairs:
            sec = sec.replace(a, b)
        return sec
    return f


def use(monkeypatch, chat, check=None, **settings):
    J._CACHE.clear()
    J._NO_THINKING.clear()
    monkeypatch.setattr("app.api.routes.knowledge_workspace.get_impact_model",
                        lambda s: LlmImpactModel(chat, name="stub:qwen", judge_chat=chat, check_chat=check))
    return settings


def go(api, monkeypatch, desc, chat, check=None, docs=None, **settings):  # noqa: F811
    use(monkeypatch, chat, check)
    seed(api, **(docs or {"ds": ("Data-Science", DS), "web": ("Web Development", WEB)}))
    for k, v in settings.items():
        setattr(api.app.state.settings, k, v)
    req = analysed(api, desc)
    return req, A.item(req, "ds") if any(i["doc_id"] == "ds" for i in req["items"]) else None


def events(req, stage=None, outcome=None, doc=None):
    ev = (req["analysis"]["trace"] or {})["events"]
    return [e for e in ev if (stage is None or e["stage"] == stage) and (outcome is None or e["outcome"] == outcome) and (doc is None or e.get("doc") == doc)]


# ======================================================================================================================== the reported scenarios
def test_scenario_A_remove_education_details_is_proposed(api, monkeypatch):  # noqa: F811
    req, it = go(api, monkeypatch, "Remove the education details of santhosh sir", Model(sub((" He do have a M.Tech in Data Science.", ""))))
    assert it["classification"] == "potential" and it["ai"]["edits"]
    assert "M.Tech" not in it["proposed_content"] and "Previously he worked in IT." in it["proposed_content"] and "3 years" in it["proposed_content"]
    assert release(api, req)["status"] == "published" and "M.Tech" not in A.live_text("ds")


def test_scenario_B_changing_the_past_industry_on_request_is_no_longer_blocked_by_the_history_guard(api, monkeypatch):  # noqa: F811
    desc = ("Change the industry in which santhosh sir worked to Mechanical. That is whatever mentioned as the field in which santhosh sir worked previously in should be changed "
            "towards Mechanical Field rather than any other field.")
    req, it = go(api, monkeypatch, desc, Model(sub(("Previously he worked in IT.", "Previously he worked in the Mechanical field."))))
    assert it["classification"] == "potential" and "Previously he worked in the Mechanical field." in it["proposed_content"]
    assert any("explicitly corrects" in f for f in it["ai"]["edits"][0]["flags"])                    # allowed, and a person is told why
    assert not events(req, "validation", "rejected")


def test_scenario_C_a_correction_that_keeps_the_contradicted_statement_is_retried_with_the_reason(api, monkeypatch):  # noqa: F811
    first = sub(("Previously he worked in IT.", "Previously he worked in a mechanical company as a support officer."),
                ("He do have a M.Tech in Data Science.", "He do have a B.Tech in AI and a M.Tech in Data Science."))
    fixed = sub(("Previously he worked in IT.", "Previously he worked in a mechanical company as a support officer."),
                ("He do have a M.Tech in Data Science.", "He has only a B.Tech in AI."))
    check = Model(lambda sec: sec)
    check.invoke = lambda msgs: AIMessage("FAIL - the M.Tech is kept although the request says only a B.Tech" if "M.Tech in Data Science" in msgs[0].content.split("<revised>")[-1]
                                          else "PASS - matches the request")
    chat = Model(first, fixed)
    req, it = go(api, monkeypatch, "Santhosh sir actually has only a B.Tech in AI and worked in a mechanical company as a support officer.", chat, check)
    assert len(chat.calls) == 2 and "YOUR PREVIOUS ANSWER WAS NOT ACCEPTED" in chat.calls[1] and "M.Tech is kept" in chat.calls[1]
    assert "M.Tech" not in it["proposed_content"] and "only a B.Tech in AI" in it["proposed_content"] and "support officer" in it["proposed_content"]
    assert events(req, "retry", "asking_again") and events(req, "semantic_check", "fail") and events(req, "semantic_check", "pass")


def test_a_value_change_does_not_rewrite_a_dated_record_even_with_a_previously_in_the_request(api, monkeypatch):  # noqa: F811
    chat = Model(sub(("10000", "12000")))
    req, it = go(api, monkeypatch, "change the fee from 10000 (previously) to 12000", chat, docs={"ds": ("Data-Science", HIST)})
    assert it is not None and "In 2021 the fee was 10000." in it["proposed_content"] and "The fee is 12000." in it["proposed_content"]


# ============================================================================================================== model behaviour the app must survive
def test_an_instruct_model_that_cannot_think_is_asked_again_without_thinking(api, monkeypatch):  # noqa: F811
    chat = Model(sub((" He do have a M.Tech in Data Science.", "")), thinks=False)
    req, it = go(api, monkeypatch, "Remove the education details of santhosh sir", chat)
    assert it["ai"]["edits"] and events(req, "model_call", "retry_without_thinking") and events(req, "model_call", "answered")
    assert events(req, "model_call", "answered")[0]["config"].get("reasoning") is False


def test_a_timeout_is_reported_as_a_model_failure_not_as_no_change(api, monkeypatch):  # noqa: F811
    req, it = go(api, monkeypatch, "Remove the education details of santhosh sir", Model(error=TimeoutError("timed out")))
    assert not it["ai"]["edits"] and "did not answer within" in it["ai"]["reason"]
    assert events(req, "model_call", "model_failed")[0]["why"].startswith("the model did not answer within")


def test_truncated_and_malformed_answers_are_named_in_the_trace(api, monkeypatch):  # noqa: F811
    chat = Model(lambda sec: sec)
    chat.invoke = lambda msgs: AIMessage("```text\nhalf" if "<section>" in msgs[0].content else "{}")
    req, it = go(api, monkeypatch, "Remove the education details of santhosh sir", chat)
    assert events(req, "model_call", "truncated") and not it["ai"]["edits"] and "cut off" in it["ai"]["reason"]


# =========================================================================================================================== the trace itself
def test_the_trace_explains_every_stage_and_holds_no_text_unless_switched_on(api, monkeypatch, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger="supportgraph.knowledge.trace")
    req, it = go(api, monkeypatch, "Remove the education details of santhosh sir", Model(sub((" He do have a M.Tech in Data Science.", ""))))
    stages = [e["stage"] for e in events(req)]
    for st in ("request", "understanding", "discovery", "passages", "context", "model_call", "validation", "classification", "analysis"):
        assert st in stages, st
    assert events(req, "discovery", "nominated", "ds")[0]["why"]
    assert events(req, "context", "built", "ds")[0]["sections"][0]["tokens"] > 0
    assert events(req, "model_call", "answered", "ds")[0]["config"]["reasoning"] is True
    assert events(req, "classification", "potential", "ds")
    assert all("text" not in e for e in events(req)) and req["analysis"]["trace"]["content"] is False
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "kb.trace" in logged and "M.Tech" not in logged and "Santhosh sir. He has" not in logged         # logs never carry document text


def test_with_content_tracing_the_prompt_and_answer_are_stored_for_the_reviewer(api, monkeypatch):  # noqa: F811
    req, _ = go(api, monkeypatch, "Remove the education details of santhosh sir", Model(sub((" He do have a M.Tech in Data Science.", ""))), kb_ai_trace_content=True)
    call = events(req, "model_call", "answered", "ds")[0]
    assert "<section>" in call["text"]["prompt"] and "```text" in call["text"]["answer"] and req["analysis"]["trace"]["content"] is True


def test_the_trace_distinguishes_why_nothing_happened(api, monkeypatch):  # noqa: F811
    # unrelated document: not discovered / no passage; the editor switched off: model not invoked
    use(monkeypatch, Model(sub(("x", "x"))))
    seed(api, ds=("Data-Science", DS), web=("Web Development", WEB))
    api.app.state.settings.kb_ai_discovery = "v2"
    api.app.state.settings.kb_ai_judge = "off"
    req = A.analysed(api, "Remove the education details of santhosh sir")
    assert events(req, "context", "model_not_invoked", "ds") and not A.item(req, "ds")["ai"]["edits"]
    use(monkeypatch, Model(lambda sec: sec))
    req2 = analysed(api, "Remove the education details of santhosh sir")
    assert events(req2, "validation", "no_change", "ds") and events(req2, "classification", "related_no_change", "ds")
    use(monkeypatch, Model(sub(("15000", "99999"))))
    req3 = analysed(api, "Remove the education details of santhosh sir")
    rej = events(req3, "validation", "rejected", "ds")
    assert rej and "figures" in rej[0]["rejected"][0]
    summary = events(req3, "discovery", "summary")[0]
    assert "web" in summary["no_signal_docs"] and summary["nominated"] >= 1


def test_the_trace_is_visible_to_the_author_and_not_in_list_views(api, monkeypatch):  # noqa: F811
    req, _ = go(api, monkeypatch, "Remove the education details of santhosh sir", Model(sub((" He do have a M.Tech in Data Science.", ""))))
    assert req["analysis"]["trace"]["events"]
    lst = api.get(f"{A.ST}/changes", headers=A.staff(api)).json()
    rows = lst["items"]
    assert rows
    assert all((r.get("analysis") or {}).get("trace") is None for r in rows)
