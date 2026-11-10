"""Helpers for the real-world failure reported against Part 2: "The machine learning faculty is changed to santhosh sir from today" against a document that says
"Machine Learning faculty is Jyothi sir". A weak local model is emulated by a stub chat whose structured answers are exactly the shapes small models produce."""

import re

from app.knowledge.impact.evaluate import Evaluation, LlmImpactModel, PassageVerdict
from app.knowledge.impact.spec import ChangeSpec, Fact
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import go_live

CHANGE = "The machine learning faculty is changed to santhosh sir from today"
DS_DOC = "Machine Learning faculty is Jyothi sir"
COURSE_DOC = "The Python Full Stack course runs for 8 months."


class Chain:
    def __init__(self, fn):
        self.fn = fn

    def invoke(self, messages):
        return self.fn(messages)


class WeakChat:
    """Emulates a small chat model: `spec` is what it extracts from the description; `verdict` builds its answer for each numbered passage in the prompt."""

    def __init__(self, spec, verdict):
        self.spec, self.verdict, self.prompts = spec, verdict, []

    def with_structured_output(self, schema):
        def run(messages):
            text = "\n".join(str(getattr(m, "content", m)) for m in messages)
            self.prompts.append(text)
            if schema is ChangeSpec:
                if isinstance(self.spec, Exception):
                    raise self.spec
                return self.spec
            out = []
            for m in re.finditer(r'<passage n="(\d+)"[^>]*>\n(.*?)\n</passage>', text, re.S):
                out.append(self.verdict(int(m.group(1)), m.group(2)))
            return Evaluation(verdicts=[v for v in out if v is not None])
        return Chain(run)


# what a small model plausibly returns -------------------------------------------------------------------------------------------------
def spec_phrase() -> ChangeSpec:
    """Entity terms as one PHRASE, the subject used as the old value, the new value written with its date word."""
    return ChangeSpec(summary=CHANGE, facts=[Fact(attribute="faculty", old_value="Machine Learning faculty", new_value="santhosh sir", effective="today")],
                      entity_terms=["machine learning faculty"])


def spec_hallucinated_old() -> ChangeSpec:
    return ChangeSpec(summary=CHANGE, facts=[Fact(attribute="faculty", old_value="Jyothi sir", new_value="santhosh sir", effective="today")], entity_terms=["machine learning", "santhosh sir"])


def spec_slot_only() -> ChangeSpec:
    """The honest reading: the description gives no old value."""
    return ChangeSpec(summary=CHANGE, facts=[Fact(attribute="faculty", old_value=None, new_value="santhosh sir")], entity_terms=["machine", "learning"])


def spec_empty() -> ChangeSpec:
    return ChangeSpec()


def says_definite(n, text):
    new = text.replace("Jyothi sir", "santhosh sir")
    return PassageVerdict(passage=n, verdict="definite", confidence=0.9, reason="The passage states the current faculty for machine learning, which must be updated.", replacement=new)


def says_no_change_but_explains_update(n, text):
    return PassageVerdict(passage=n, verdict="no_change", confidence=0.7, reason="The passage states the old value for the machine learning faculty, so this passage must be updated.")


def says_potential_without_replacement(n, text):
    return PassageVerdict(passage=n, verdict="potential", confidence=0.6, reason="Probably the same faculty.", replacement=None)


def says_irrelevant(n, text):
    return PassageVerdict(passage=n, verdict="irrelevant", confidence=0.5, reason="Unrelated.")


def seed_ds(api, extra=None):
    go_live(api, doc_id="data-science", content=DS_DOC, title="Data-Science", category="courses")
    go_live(api, doc_id="course", content=COURSE_DOC, title="course", category="courses")
    for doc_id, (title, content) in (extra or {}).items():
        go_live(api, doc_id=doc_id, content=content, title=title, category="courses")


def use_model(monkeypatch, chat, name="stub:weak"):
    from app.api.routes import knowledge_workspace as kw
    monkeypatch.setattr(kw, "get_impact_model", lambda s: LlmImpactModel(chat, name=name))


def run(api, monkeypatch, chat, description=CHANGE):
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    use_model(monkeypatch, chat)
    rid = A.start_ai(api, description)["request_id"]
    A.analyse(api, rid)
    return A.get(api, rid)
