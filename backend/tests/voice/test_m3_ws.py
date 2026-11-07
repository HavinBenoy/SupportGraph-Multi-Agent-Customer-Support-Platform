"""M3 over the real FastAPI app and the real support graph (offline fakes): the metrics message, the browser's playback acks and the status report."""

import json

from tests.api.conftest import T, api  # noqa: F401
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.voice.test_voice_ws import reply_of, say, setup_parts, start


def test_a_spoken_faq_answer_reports_four_latencies_and_the_graph_breakdown_over_the_socket(api):  # noqa: F811
    setup_parts(api, ["Can I pay the course fee in instalments?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, _ = say(ws)
    kinds = [m["type"] for m in msgs]
    assert kinds.index("metrics") < kinds.index("audio_end")
    t = next(m for m in msgs if m["type"] == "metrics")["timing"]
    assert t["endpoint_to_graph_done_ms"] <= t["endpoint_to_answer_audio_ms"] <= t["endpoint_to_playback_done_ms"]
    assert {"embed", "search", "llm", "validate"} <= set(t["stages"]) and set(t["nodes"]) >= {"triage", "knowledge", "qa"}
    assert t["cold"] in (True, False) and t["unaccounted_ms"] >= -1
    r = reply_of(msgs)
    assert r["perf"]["graph_ms"] >= 0 and r["timing"]["endpoint_to_graph_done_ms"] >= 0


def test_the_browser_playback_acks_are_accepted_and_do_not_disturb_the_call(api):  # noqa: F811
    setup_parts(api, ["Can I pay the course fee in instalments?", "Thanks"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, _ = say(ws)
        turn = reply_of(msgs)["turn_id"]
        for state, extra in (("started", {"filler": False}), ("started", {"filler": True}), ("started", {}), ("ended", {}), ("started", {"filler": "x"}),
                             ("nonsense", {})):
            ws.send_text(json.dumps({"type": "playback", "turn_id": turn, "state": state, **extra}))
        ws.send_text(json.dumps({"type": "playback", "turn_id": "bad", "state": "started"}))
        msgs2, audio = say(ws)                                   # the call still works
    assert reply_of(msgs2)["turn_id"] == turn + 1 and audio > 0


def test_voice_status_lists_the_warm_up_state_and_is_a_no_op_for_fake_providers(api):  # noqa: F811
    j = api.get("/api/voice/status").json()
    assert j["warm"] is None and j["warm_detail"] == {"llm": None, "voice": None, "steps": {}}
