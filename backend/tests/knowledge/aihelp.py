"""HTTP shortcuts for the Part 2 (AI-assisted change) tests. Analysis and release run inline (kb_ai_background = False) so every test is deterministic."""

from tests.knowledge.kbhelp import BASE, admin, live_chunks
from tests.knowledge.kwhelp import ST, staff

FEE_CHANGE = "The Python Full Stack course fee changed from ₹45,000 to ₹48,000 starting 1 December."


def inline(api):
    s = api.app.state.settings
    s.kb_ai_background = False
    s.kb_ai_mode = "rules"
    return s


def start_ai(api, description=FEE_CHANGE, h=None, expect=201, **kw):
    r = api.post(f"{ST}/ai-changes", json={"description": description, **kw}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def analyse(api, rid, h=None, expect=202, **body):
    r = api.post(f"{ST}/changes/{rid}/analyse", json=body, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def get(api, rid, h=None, expect=200):
    r = api.get(f"{ST}/changes/{rid}", headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def analysed(api, description=FEE_CHANGE, h=None, **kw):
    inline(api)
    rid = start_ai(api, description, h=h, **kw)["request_id"]
    analyse(api, rid, h=h)
    return get(api, rid, h=h)


def item(req, doc_id):
    return next(i for i in req["items"] if i["doc_id"] == doc_id)


def decide(api, rid, doc_id, decision, h=None, expect=200, **kw):
    r = api.post(f"{ST}/changes/{rid}/items/{doc_id}/decision", json={"decision": decision, **kw}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def accept_all(api, req, h=None):
    for it in req["items"]:
        if it["actionable"] and it["ai"] and (it["ai"].get("edits") or []):
            decide(api, req["request_id"], it["doc_id"], "accepted", h=h)
    return get(api, req["request_id"], h=h)


def submit(api, rid, desc=None, h=None, expect=200, **kw):
    r = api.post(f"{ST}/changes/{rid}/submit", json={"description": desc or "Python fee increase", **kw}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def a_get(api, rid, h=None, expect=200):
    r = api.get(f"{BASE}/changes/{rid}", headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def a_post(api, rid, path, body=None, h=None, expect=200):
    r = api.post(f"{BASE}/changes/{rid}/{path}", json=body or {}, headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def approve(api, rid, h=None, expect=200, note=None):
    return a_post(api, rid, "review", {"decision": "approve", **({"note": note} if note else {})}, h=h, expect=expect)


def publish(api, rid, h=None, expect=202):
    return a_post(api, rid, "publish", None, h=h, expect=expect)


def live_text(doc_id):
    return "\n".join(t for _, t in live_chunks(doc_id))
