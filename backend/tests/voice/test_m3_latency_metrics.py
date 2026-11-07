"""M3: the four voice latencies are distinct, correctly ordered and never mix a holding phrase up with the answer.
Fake STT/TTS: this proves the bookkeeping and the message order, not real speech speed."""

import asyncio
import time

import pytest

from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from tests.voice.audio import utterance
from tests.voice.test_turns import OK, SlowTts, feed, frames, mk, msgs, n_of, until

PERF = {"cold": True, "cold_reasons": ["llm_load"], "unaccounted_ms": 7, "stages": {"embed": 55.0, "llm": 3200.0}, "llm_load_ms": 4100.0}


def reply_with_perf(text):
    return {"reply": "The fee is 45,000 INR.", **OK, "perf": PERF, "node_ms": {"triage": 100, "knowledge": 300}}


async def spoken_turn(v, tl, n=1):
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "audio_end") >= n)


@pytest.mark.anyio
async def test_without_a_holding_phrase_first_audio_and_answer_audio_are_the_same_event():
    v, tl, _ = mk(turn=reply_with_perf, stt=FakeStt(["What is the fee for Python?"]))
    await spoken_turn(v, tl)
    t = msgs(tl, "metrics")[0]["timing"]
    assert t["endpoint_to_first_audio_ms"] == t["endpoint_to_answer_audio_ms"] and "endpoint_to_filler_ms" not in t
    assert t["endpoint_to_graph_done_ms"] <= t["endpoint_to_answer_audio_ms"] <= t["endpoint_to_playback_done_ms"]
    assert t["answer_audio_ms"] > 0 and t["endpoint_to_reply_ms"] == t["endpoint_to_graph_done_ms"]
    await v.close()


@pytest.mark.anyio
async def test_a_holding_phrase_is_first_audio_but_never_reported_as_the_answer():
    def slow(text):
        time.sleep(0.5)
        return reply_with_perf(text)

    v, tl, _ = mk(turn=slow, stt=FakeStt(["What is the fee for Python?"]), voice_filler_after_ms=150)
    await spoken_turn(v, tl, n=2)
    ms = msgs(tl, "metrics")
    assert len(ms) == 1                                    # one report per ANSWER; the holding phrase has none
    t = ms[0]["timing"]
    assert t["endpoint_to_first_audio_ms"] == t["endpoint_to_filler_ms"] < 500          # the phrase was heard long before the answer existed
    assert t["endpoint_to_graph_done_ms"] >= 500
    assert t["endpoint_to_answer_audio_ms"] >= t["endpoint_to_graph_done_ms"] > t["endpoint_to_filler_ms"]
    assert t["endpoint_to_playback_done_ms"] >= t["endpoint_to_answer_audio_ms"]
    await v.close()


@pytest.mark.anyio
async def test_graph_breakdown_and_cold_flag_travel_with_the_turn_timing():
    v, tl, _ = mk(turn=reply_with_perf, stt=FakeStt(["What is the fee for Python?"]))
    await spoken_turn(v, tl)
    t = msgs(tl, "metrics")[0]["timing"]
    assert t["cold"] is True and t["cold_reasons"] == ["llm_load"] and t["unaccounted_ms"] == 7
    assert t["stages"] == {"embed": 55.0, "llm": 3200.0} and t["llm_load_ms"] == 4100.0 and t["nodes"] == {"triage": 100, "knowledge": 300}
    graph_done = next(e for e in v.events if e["event"] == "graph_done")
    assert graph_done["cold"] is True and graph_done["stages"] == {"embed": 55.0, "llm": 3200.0}
    await v.close()


@pytest.mark.anyio
async def test_metrics_come_before_audio_end_which_stays_the_last_message_of_a_response():
    v, tl, _ = mk(turn=reply_with_perf, stt=FakeStt(["What is the fee for Python?"]))
    await spoken_turn(v, tl)
    kinds = [m["type"] for m in msgs(tl)]
    assert kinds.index("metrics") < kinds.index("audio_end") == len(kinds) - 1
    await v.close()


class SteadyTts(FakeTts):
    """20 chunks, 25 ms apart: a window long enough for the browser's acks to arrive while the answer is being sent."""

    def synthesize(self, text):
        self.spoken.append(text)
        for _ in range(20):
            time.sleep(0.025)
            yield b"\x01\x00" * 1000


@pytest.mark.anyio
async def test_the_browser_started_ack_gives_first_audible_and_a_late_ack_for_the_other_response_is_ignored():
    v, tl, _ = mk(turn=reply_with_perf, stt=FakeStt(["What is the fee for Python?"]), tts=SteadyTts())
    await v.start()
    await feed(v, utterance())
    await until(lambda: len(frames(tl)) >= 2)
    v.playback_started(1, True)       # an ack that belongs to a holding phrase must not be credited to the answer
    v.playback_started(7, False)      # another turn: ignored
    v.playback_started(1, False)      # the answer is audible
    first = None
    for e in v.events:
        if e["event"] == "playback_audible":
            first = e
    assert first and first["filler"] is False
    v.playback_started(1, False)      # a duplicate ack changes nothing
    await until(lambda: n_of(tl, "audio_end") == 1)
    t = msgs(tl, "metrics")[0]["timing"]
    assert t["endpoint_to_first_audible_ms"] == t["endpoint_to_answer_audible_ms"] >= t["endpoint_to_first_audio_ms"]
    assert sum(1 for e in v.events if e["event"] == "playback_audible") == 1
    await v.close()


@pytest.mark.anyio
async def test_an_interrupted_answer_reports_no_playback_metrics():
    def slow(text):
        return {"reply": "An answer.", **OK}

    v, tl, _ = mk(turn=slow, tts=SlowTts(), stt=FakeStt(["What is the fee for Python?", "What is the duration of Java?"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: len(frames(tl)) >= 2)
    await v.submit_text("What is the duration of Java?")
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking, timeout=8)
    assert not any(m["turn_id"] == 1 for m in msgs(tl, "metrics"))
    await v.close()


@pytest.mark.anyio
async def test_a_stale_reply_that_is_never_spoken_reports_no_metrics():
    def slow(text):
        time.sleep(0.3)
        return reply_with_perf(text)

    v, tl, _ = mk(turn=slow, voice_filler_after_ms=0)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await v.submit_text("Actually what is the duration?")
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking)
    assert [m["turn_id"] for m in msgs(tl, "metrics")] == [] or all(m["turn_id"] == 2 for m in msgs(tl, "metrics"))
    await v.close()
    await asyncio.sleep(0)


# ---- fixed holding phrases are rendered once per speech engine; answers never are ------------------------------------------------------------
def slow_reply(text):
    time.sleep(0.4)
    return {"reply": "The fee is 45,000 INR.", **OK}


async def one_filler_turn(tts, **kw):
    v, tl, _ = mk(turn=slow_reply, tts=tts, voice_filler_after_ms=100, **kw)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await until(lambda: n_of(tl, "audio_end") == 2)
    await v.close()
    return tl


@pytest.mark.anyio
async def test_a_holding_phrase_is_synthesised_once_and_replayed_identically_while_the_answer_is_always_fresh():
    from app.voice.session import FILLERS

    tts = FakeTts()
    tl1 = await one_filler_turn(tts)
    tl2 = await one_filler_turn(tts)                       # a new call on the same speech engine
    filler_syntheses = [x for x in tts.spoken if x in FILLERS]
    answers = [x for x in tts.spoken if x not in FILLERS]
    assert len(filler_syntheses) == 1                      # the second call replayed the stored audio
    assert len(answers) == 2                               # ... but the answer was synthesised both times
    f1 = [m["bytes"] for m in frames(tl1) if m["turn_id"] == 1][:1]
    assert [m["bytes"] for m in frames(tl2)][:1] == f1 and [bool(m.get("filler")) for m in msgs(tl2, "audio_start")] == [True, False]


class GatedTts(FakeTts):
    """The first chunk arrives at once; the rest only after `gate` is set (so a cut can happen in the middle of synthesising a phrase)."""

    def __init__(self):
        super().__init__()
        import threading
        self.gate = threading.Event()

    def synthesize(self, text):
        self.spoken.append(text)
        yield b"\x01\x00" * 500
        if not self.gate.wait(1.5):
            return
        for _ in range(5):
            yield b"\x01\x00" * 500


@pytest.mark.anyio
async def test_a_holding_phrase_cut_while_it_is_being_rendered_is_never_stored():
    from app.voice import session as sess

    tts = GatedTts()
    v, tl, _ = mk(turn=slow_reply, tts=tts, voice_filler_after_ms=100)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await until(lambda: len(frames(tl)) >= 1)
    await v.submit_text("Never mind, what about Java?")   # a newer question cuts the phrase half-way through its rendering
    await until(lambda: any(m.get("interrupted") for m in msgs(tl, "audio_end")), timeout=8)
    tts.gate.set()
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking, timeout=8)
    await v.close()
    assert not sess._FILLER_AUDIO.get(tts, {}).get(next(x for x in tts.spoken if x in sess.FILLERS))   # a clipped phrase would be replayed clipped forever


@pytest.mark.anyio
async def test_the_setting_turns_the_filler_cache_off():
    from app.voice.session import FILLERS

    tts = FakeTts()
    await one_filler_turn(tts, voice_cache_fillers=False)
    await one_filler_turn(tts, voice_cache_fillers=False)
    assert sum(1 for x in tts.spoken if x in FILLERS) == 2


def test_prerendering_fills_the_store_so_the_first_holding_phrase_needs_no_synthesis():
    from app.voice.session import FILLERS, _FILLER_AUDIO, prerender_fillers

    tts = FakeTts()
    assert prerender_fillers(tts) == len(FILLERS) and set(_FILLER_AUDIO[tts]) == set(FILLERS)
    n = len(tts.spoken)
    assert prerender_fillers(tts) == len(FILLERS) and len(tts.spoken) == n          # idempotent: nothing synthesised again
