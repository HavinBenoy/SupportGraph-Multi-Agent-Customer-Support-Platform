"""M2.2: voice turn management. Utterance aggregation, one graph run at a time, one spoken response at a time, stale replies, barge-in, cleanup.
Fake STT/TTS/VAD (scripted transcripts, tone audio, energy VAD): this proves the logic and the guarantees, not recognition quality or real speakers."""

import asyncio
import time

import pytest

from app.config import Settings
from app.voice.session import AUDIO_HEADER, VoiceSession
from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from app.voice.turns import COMPLETE, INCOMPLETE, SHORT, Turn, completeness, grace_ms
from app.voice.vad import EnergyVad
from tests.voice.audio import chunks, silence, tone, utterance

OK = {"status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t1"}


def mk(turn=None, stt=None, tts=None, **kw):
    """A session whose JSON events and audio frames land in ONE timeline, so ordering can be asserted."""
    s = Settings(_env_file=None, voice_provider="fake", **kw)
    tl, calls = [], []

    async def sj(m):
        tl.append(("json", m))

    async def sb(b):
        turn_id, seq = AUDIO_HEADER.unpack(b[:8])
        tl.append(("audio", {"turn_id": turn_id, "seq": seq, "bytes": len(b) - 8}))

    def default_turn(text):
        calls.append(text)
        return {"reply": f"Answer to {text}.", **OK}

    def wrapped(text):
        calls.append(text)
        return turn(text)

    v = VoiceSession(s=s, vad=EnergyVad(), stt=stt or FakeStt(["what is the fee"]), tts=tts or FakeTts(), run_turn=wrapped if turn else default_turn,
                     send_json=sj, send_bytes=sb)
    return v, tl, calls


def msgs(tl, kind=None):
    return [m for k, m in tl if k == "json" and (kind is None or m["type"] == kind)]


def frames(tl):
    return [m for k, m in tl if k == "audio"]


async def feed(v, pcm):
    for c in chunks(pcm):
        await v.feed_audio(c)


async def until(cond, timeout=5.0):
    async def wait():
        while not cond():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)


def n_of(tl, kind):
    return len(msgs(tl, kind))


class SlowTts(FakeTts):
    """~40 chunks, 10 ms apart: long enough to interrupt."""

    def synthesize(self, text):
        self.spoken.append(text)
        for _ in range(40):
            yield b"\x01\x00" * 2000
            time.sleep(0.01)


# ---- completeness / grace --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    ("What is the fee structure for Python Full Stack?", COMPLETE), ("Thank you", COMPLETE), ("Bye", COMPLETE), ("", COMPLETE),
    ("I want to know about the course", INCOMPLETE), ("I want to know about the course...", INCOMPLETE), ("What is the fee for", INCOMPLETE),
    ("I have a question", INCOMPLETE), ("tell me about the fees and", INCOMPLETE), ("Python,", INCOMPLETE),
    ("python fees", SHORT), ("fees", SHORT),
])
def test_completeness(text, kind):
    assert completeness(text) == kind


def test_grace_is_longer_when_the_sentence_is_unfinished():
    s = Settings(_env_file=None)
    assert grace_ms(s, "What is the fee?") < grace_ms(s, "python fees") < grace_ms(s, "I want to know about the")
    assert s.voice_silence_ms <= 700            # the silence threshold was NOT simply raised to cover pauses


def test_turn_describe_has_boundaries_but_no_words_or_audio():
    t = Turn(id=3, segments=[b"\x00" * 10, b"\x00" * 20], segment_ms=[400, 700], text="secret words")
    d = t.describe()
    assert d["segment_ms"] == [400, 700] and d["audio_ms"] == 1100 and d["merged"] is True
    assert "secret" not in str(d) and "segments" in d


# ---- utterance management ---------------------------------------------------------------------------------------------------------------
def staged(first="I want to know about the course", whole="I want to know about the course duration and eligibility"):
    return FakeStt(lambda a: first if len(a) < 70000 else whole)


@pytest.mark.anyio
async def test_a_pause_inside_a_sentence_is_one_turn_and_one_graph_run():
    v, tl, calls = mk(stt=staged())
    await v.start()
    await feed(v, tone(700) + silence(800) + tone(700) + silence(900))   # 0.8 s pause > silence_ms: two VAD segments
    await until(lambda: n_of(tl, "audio_end") >= 1)
    await asyncio.sleep(0.4)
    assert calls == ["I want to know about the course duration and eligibility"]    # the merged audio was transcribed, once, as one request
    assert v.stats["graph_runs"] == 1 and v.stats["segments"] == 2 and v.stats["merged_turns"] == 1
    assert n_of(tl, "reply") == 1 and n_of(tl, "audio_start") == 1
    reply = msgs(tl, "reply")[0]
    assert reply["turn"]["segments"] == 2 and reply["turn"]["merged"] and reply["turn"]["kind"] == "continuation" and reply["stale"] is False
    await v.close()


@pytest.mark.anyio
async def test_segment_boundaries_are_kept_for_debugging():
    v, tl, _ = mk(stt=staged())
    await v.start()
    await feed(v, tone(700) + silence(800) + tone(700) + silence(900))
    await until(lambda: n_of(tl, "reply") >= 1)
    final = [e for e in v.events if e["event"] == "turn_final"][0]
    assert final["segments"] == 2 and len(final["segment_ms"]) == 2 and final["merged"] is True
    assert sum(1 for e in v.events if e["event"] == "vad_endpoint") == 2
    await v.close()


@pytest.mark.anyio
async def test_many_frames_of_one_utterance_are_one_segment():
    v, tl, calls = mk()
    await v.start()
    await feed(v, utterance(2500, 900))                          # 100+ twenty-millisecond frames
    await until(lambda: n_of(tl, "audio_end") >= 1)
    assert v.stats["segments"] == 1 and len(calls) == 1
    await v.close()


@pytest.mark.anyio
async def test_a_completed_utterance_is_not_held_back_for_long():
    v, tl, calls = mk(stt=FakeStt(["What is the fee for Python Full Stack?"]))
    await v.start()
    t0 = time.monotonic()
    await feed(v, utterance())
    await until(lambda: calls)
    assert time.monotonic() - t0 < 1.0                           # complete sentence: only the short grace period, not the incomplete one
    await v.close()


@pytest.mark.anyio
async def test_an_unfinished_sentence_waits_longer_than_a_finished_one():
    v, tl, calls = mk(stt=FakeStt(["I want to know about the"]), voice_grace_incomplete_ms=900)
    await v.start()
    t0 = time.monotonic()
    await feed(v, utterance())
    await until(lambda: calls)
    assert time.monotonic() - t0 >= 0.85
    await v.close()


@pytest.mark.anyio
async def test_a_turn_is_sealed_at_the_maximum_length(monkeypatch):
    monkeypatch.setattr("app.voice.session.BURST_MS", 120_000)
    v, tl, calls = mk(voice_max_utterance_s=3, voice_max_turn_s=5, stt=FakeStt(["a long ramble about the courses."]))
    await v.start()
    await feed(v, tone(7000) + silence(900))
    await until(lambda: calls)
    assert any(e["event"] == "vad_endpoint" and e.get("sealed") for e in v.events)
    await until(lambda: v.stats["turns_finalized"] >= 2)
    finals = [e for e in v.events if e["event"] == "turn_final"]
    assert [e["turn_id"] for e in finals] == [1, 2] and finals[0]["audio_ms"] >= 5000   # the sealed turn was committed, then the remainder
    await v.close()


@pytest.mark.anyio
async def test_silence_without_speech_creates_no_turn():
    v, tl, calls = mk()
    await v.start()
    await feed(v, silence(2000))
    await asyncio.sleep(0.2)
    assert tl == [] and calls == [] and v.stats["segments"] == 0
    await v.close()


@pytest.mark.anyio
async def test_two_separate_questions_are_two_turns_with_two_sequential_answers():
    v, tl, calls = mk(stt=FakeStt(["What is the fee for Python?", "When does the batch start?"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "audio_end") >= 1)
    await asyncio.sleep(0.4)                                      # a real gap between two questions
    await feed(v, utterance())
    await until(lambda: n_of(tl, "audio_end") >= 2)
    assert calls == ["What is the fee for Python?", "When does the batch start?"]
    assert [m["turn_id"] for m in msgs(tl, "reply")] == [1, 2]
    assert v.stats["merged_turns"] == 0 and v.stats["max_concurrent_graph"] == 1
    await v.close()


@pytest.mark.anyio
async def test_speech_starting_during_the_grace_period_holds_the_turn():
    v, tl, calls = mk(stt=staged(), voice_grace_incomplete_ms=1500)
    await v.start()
    await feed(v, tone(700) + silence(700))
    await asyncio.sleep(0.3)                                      # still inside the grace period: nothing has been sent to the graph
    assert calls == []
    await feed(v, tone(700) + silence(900))
    await until(lambda: calls)
    assert calls == ["I want to know about the course duration and eligibility"] and v.stats["graph_runs"] == 1
    await v.close()


# ---- one graph run at a time ------------------------------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_two_turns_arriving_together_never_run_the_graph_concurrently():
    active, peak = [0], [0]

    def slow(text):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        active[0] -= 1
        return {"reply": f"Answer to {text}.", **OK}

    v, tl, calls = mk(turn=slow)
    await v.start()
    await asyncio.gather(v.submit_text("first question about fees"), v.submit_text("second question about batches"))
    await until(lambda: v.stats["graph_runs"] == 2 and not v.speaking and n_of(tl, "reply") == 2)
    assert peak[0] == 1 and v.stats["max_concurrent_graph"] == 1
    await v.close()


@pytest.mark.anyio
async def test_the_older_answer_is_stale_when_a_newer_question_exists():
    def slow(text):
        time.sleep(0.2)
        return {"reply": f"Answer to {text}.", **OK}

    tts = FakeTts()
    v, tl, _ = mk(turn=slow, tts=tts)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await asyncio.sleep(0.05)
    await v.submit_text("Actually what is the duration?")
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking)
    r1, r2 = msgs(tl, "reply")
    assert r1["stale"] is True and r2["stale"] is False and v.stats["stale_replies"] == 1
    assert n_of(tl, "audio_start") == 1 and msgs(tl, "audio_start")[0]["turn_id"] == 2       # only the latest is spoken
    assert any("duration" in s for s in tts.spoken) and not any("fee" in s for s in tts.spoken)
    assert any(e["event"] == "reply_stale" and e["turn_id"] == 1 for e in v.events)
    await v.close()


@pytest.mark.anyio
async def test_a_light_follow_up_does_not_invalidate_the_answer_on_its_way():
    def slow(text):
        time.sleep(0.2)
        return {"reply": f"Answer to {text}.", **OK}

    v, tl, _ = mk(turn=slow)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await asyncio.sleep(0.05)
    await v.submit_text("okay")                                   # an acknowledgement is not a new question
    await until(lambda: n_of(tl, "reply") == 2 and n_of(tl, "audio_end") == 2)
    assert [m["stale"] for m in msgs(tl, "reply")] == [False, False]
    await v.close()


@pytest.mark.anyio
async def test_a_handoff_is_spoken_even_if_a_newer_question_arrived():
    def turn(text):
        time.sleep(0.15)
        if "human" in text:
            return {"reply": "I have passed this to our support team.", "status": "escalated", "citations": [], "escalation_id": "ESC-1", "ticket_id": "t1"}
        return {"reply": "Some answer.", **OK}

    v, tl, _ = mk(turn=turn)
    await v.start()
    await v.submit_text("I need a human please")
    await asyncio.sleep(0.03)
    await v.submit_text("and what is the fee for Python?")
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking)
    assert msgs(tl, "reply")[0]["stale"] is False and msgs(tl, "audio_start")[0]["turn_id"] == 1
    await v.close()


# ---- one spoken response at a time ------------------------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_playback_is_sequential_and_frames_belong_to_their_turn():
    v, tl, _ = mk()
    await v.start()
    await v.submit_text("okay")
    await v.submit_text("thanks")
    await until(lambda: n_of(tl, "audio_end") == 2)
    order = [(k, m["type"] if k == "json" else m["turn_id"]) for k, m in tl if k == "audio" or m["type"] in ("audio_start", "audio_end")]
    open_turn, seen = None, []
    for k, x in order:
        if k == "json" and x == "audio_start":
            assert open_turn is None, "a response started while another was still open"
            open_turn = "open"
        elif k == "json" and x == "audio_end":
            open_turn = None
        else:
            assert open_turn is not None, "audio outside an audio_start/audio_end pair"
            seen.append(x)
    assert seen == sorted(seen) and set(seen) == {1, 2}
    for t in (1, 2):
        seqs = [f["seq"] for f in frames(tl) if f["turn_id"] == t]
        assert seqs == list(range(len(seqs)))
    await v.close()


@pytest.mark.anyio
async def test_two_responses_ready_at_the_same_instant_do_not_overlap():
    v, tl, _ = mk(tts=SlowTts())
    await v.start()
    t1, t2 = Turn(id=1, text="a", typed=True), Turn(id=2, text="b", typed=True)
    await asyncio.gather(v._speak(t1, "First answer here.", {}), v._speak(t2, "Second answer here.", {}))
    starts = [i for i, (k, m) in enumerate(tl) if k == "json" and m["type"] == "audio_start"]
    ends = [i for i, (k, m) in enumerate(tl) if k == "json" and m["type"] == "audio_end"]
    assert len(starts) == 2 and starts[0] < ends[0] < starts[1] < ends[1]
    assert v.stats["overlap_prevented"] == 1 and any(e["event"] == "overlap_prevented" for e in v.events)
    await v.close()


@pytest.mark.anyio
async def test_a_new_turn_during_tts_stops_the_old_audio_and_only_the_new_one_plays():
    v, tl, _ = mk(tts=SlowTts(), voice_barge_in_ms=96)
    await v.start()
    await v.submit_text("tell me about the python course")
    await until(lambda: len(frames(tl)) >= 3)
    await v.submit_text("What is the duration of the data science course?")
    await until(lambda: n_of(tl, "audio_end") == 2)
    intr = msgs(tl, "interrupted")
    assert len(intr) == 1 and intr[0]["turn_id"] == 1
    end1 = next(i for i, (k, m) in enumerate(tl) if k == "json" and m["type"] == "audio_end" and m["turn_id"] == 1)
    assert not any(k == "audio" and m["turn_id"] == 1 for k, m in tl[end1 + 1:])          # nothing of response 1 after it ended
    assert msgs(tl, "audio_end")[0]["interrupted"] is True and msgs(tl, "audio_end")[1]["interrupted"] is False
    assert v.stats["barge_ins"] == 1
    await v.close()


@pytest.mark.anyio
async def test_stale_chunks_after_a_cancel_are_never_sent():
    v, tl, _ = mk(tts=SlowTts())
    await v.start()
    await v.submit_text("tell me about the python course")
    await until(lambda: len(frames(tl)) >= 2)
    await v._interrupt("test")
    await until(lambda: n_of(tl, "audio_end") == 1)
    n = len(frames(tl))
    await asyncio.sleep(0.3)
    assert len(frames(tl)) == n and not v.speaking and v._speech is None
    assert [m["turn_id"] for m in msgs(tl, "interrupted")] == [1]
    await v.close()


@pytest.mark.anyio
async def test_a_cancel_cannot_leak_into_the_next_response():
    v, tl, _ = mk(tts=SlowTts())
    await v.start()
    await v.submit_text("tell me about python")
    await until(lambda: len(frames(tl)) >= 2)
    await v.submit_text("What is the fee for data science?")
    await until(lambda: n_of(tl, "audio_end") == 2)
    second = [f for f in frames(tl) if f["turn_id"] == 2]
    assert len(second) >= 20                                      # the second response was spoken in full, not cut by the first one's cancel
    await v.close()


@pytest.mark.anyio
async def test_barge_in_by_voice_stops_playback_and_the_new_turn_is_answered():
    v, tl, calls = mk(tts=SlowTts(), stt=FakeStt(["tell me about the fees", "What about the duration?"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: len(frames(tl)) >= 2)
    await feed(v, utterance(800, 900))                            # real speech over the bot
    await until(lambda: n_of(tl, "audio_end") == 2)
    assert msgs(tl, "interrupted") and msgs(tl, "interrupted")[0]["turn_id"] == 1
    assert calls[-1] == "What about the duration?" and v.stats["barge_ins"] >= 1
    assert any(e["event"] == "barge_in" for e in v.events)
    assert msgs(tl, "reply")[-1]["turn"]["kind"] in ("interruption", "continuation", "new", "")
    await v.close()


@pytest.mark.anyio
async def test_a_cough_does_not_interrupt():
    v, tl, _ = mk(tts=SlowTts(), voice_barge_in_ms=400)
    await v.start()
    await v.submit_text("tell me about the fees")
    await until(lambda: len(frames(tl)) >= 2)
    await feed(v, tone(160) + silence(200))
    await until(lambda: n_of(tl, "audio_end") == 1)
    assert not msgs(tl, "interrupted") and msgs(tl, "audio_end")[0]["interrupted"] is False
    await v.close()


@pytest.mark.anyio
async def test_a_reply_waits_while_the_caller_is_still_talking():
    def slow(text):
        time.sleep(0.4)
        return {"reply": "An answer.", **OK}

    v, tl, calls = mk(turn=slow, stt=FakeStt(["What is the fee for Python?"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "thinking") == 1)
    await feed(v, tone(300))                                      # the caller starts again while the answer is ready
    await asyncio.sleep(0.3)
    assert n_of(tl, "audio_start") == 0                           # it does not talk over the caller
    await v.close()


# ---- pacing: "speaking" lasts as long as the audio is audible -------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_server_stays_speaking_until_the_audio_has_played():
    class OneSecond(FakeTts):
        def synthesize(self, text):
            self.spoken.append(text)
            yield b"\x01\x00" * (self.sample_rate)                # 1.0 s of audio, produced instantly

    v, tl, _ = mk(tts=OneSecond(), voice_pace_audio=True, voice_pace_lead_ms=200)
    await v.start()
    t0 = time.monotonic()
    await v.submit_text("okay")
    await until(lambda: n_of(tl, "audio_end") == 1)
    assert time.monotonic() - t0 >= 0.8                           # not "done" when the bytes were sent, but when they would have played out
    await v.close()


@pytest.mark.anyio
async def test_the_browser_ack_ends_the_wait_early():
    class FiveSeconds(FakeTts):
        def synthesize(self, text):
            self.spoken.append(text)
            yield b"\x01\x00" * (self.sample_rate * 5)

    v, tl, _ = mk(tts=FiveSeconds(), voice_pace_audio=True, voice_pace_lead_ms=6000)
    await v.start()
    await v.submit_text("okay")
    await until(lambda: v.speaking and frames(tl))
    await asyncio.sleep(0.05)
    v.playback_ended(1)
    await until(lambda: n_of(tl, "audio_end") == 1, timeout=2)
    await v.close()


@pytest.mark.anyio
async def test_barge_in_cuts_paced_audio_so_little_is_left_to_play():
    class LongTts(FakeTts):
        def synthesize(self, text):
            self.spoken.append(text)
            for _ in range(60):
                yield b"\x01\x00" * (self.sample_rate // 2)       # 0.5 s each: 30 s in total

    v, tl, _ = mk(tts=LongTts(), voice_pace_audio=True, voice_pace_lead_ms=500)
    await v.start()
    await v.submit_text("tell me everything about the courses")
    await asyncio.sleep(0.6)
    await v._interrupt("test")
    await until(lambda: n_of(tl, "audio_end") == 1, timeout=2)
    sent_s = sum(f["bytes"] for f in frames(tl)) / 2 / 22050
    assert sent_s <= 3.0                                          # a few seconds at most were ever queued in the browser, never the 30 s reply
    await v.close()


# ---- disconnect and cleanup -------------------------------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_close_during_playback_cancels_everything_and_sends_nothing_afterwards():
    v, tl, _ = mk(tts=SlowTts())
    await v.start()
    await v.submit_text("tell me about the python course")
    await until(lambda: len(frames(tl)) >= 2)
    await v.close()
    n = len(tl)
    await asyncio.sleep(0.3)
    assert len(tl) == n and v._worker.done() and v._speech is None
    assert not [t for t in asyncio.all_tasks() if (t.get_name() or "").startswith("voice-")]
    assert any(e["event"] == "session_close" for e in v.events)


@pytest.mark.anyio
async def test_close_cancels_a_pending_settle_timer_and_is_idempotent():
    v, tl, calls = mk(stt=FakeStt(["I want to know about the"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "transcript") >= 1)
    await v.close()
    await v.close()
    await asyncio.sleep(1.8)
    assert calls == [] and not [t for t in asyncio.all_tasks() if (t.get_name() or "").startswith("voice-")]


@pytest.mark.anyio
async def test_close_while_the_graph_is_running_does_not_speak_afterwards():
    def slow(text):
        time.sleep(0.3)
        return {"reply": "late answer.", **OK}

    v, tl, _ = mk(turn=slow)
    await v.start()
    await v.submit_text("What is the fee?")
    await asyncio.sleep(0.05)
    await v.close()
    await asyncio.sleep(0.5)
    assert n_of(tl, "audio_start") == 0 and frames(tl) == []


@pytest.mark.anyio
async def test_events_carry_ids_and_sizes_but_never_words():
    v, tl, _ = mk(stt=FakeStt(["my attendance for student stu_asha is secretword"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "audio_end") == 1)
    kinds = {e["event"] for e in v.events}
    assert {"vad_speech_start", "vad_endpoint", "stt_done", "turn_final", "graph_start", "graph_done", "tts_start", "tts_first_audio",
            "playback_start", "playback_done"} <= kinds
    assert "secretword" not in str(list(v.events)) and "stu_asha" not in str(list(v.events))
    await v.close()


@pytest.mark.anyio
async def test_the_queue_is_bounded():
    gate = []

    def blocked(text):
        while not gate:
            time.sleep(0.01)
        return {"reply": "ok.", **OK}

    v, tl, _ = mk(turn=blocked, voice_max_pending_turns=1)
    await v.start()
    for i in range(4):
        await v.submit_text(f"question number {i} about fees")
        await asyncio.sleep(0.02)
    assert any(m.get("detail") == "Still working on your previous question." for m in msgs(tl, "notice"))
    gate.append(1)
    await v.close()


# ---- M3 slice: latency numbers and the holding phrase --------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_slow_answer_gets_one_holding_phrase_then_the_answer_in_order():
    def slow(text):
        time.sleep(0.5)
        return {"reply": "The fee is 45,000 INR.", **OK}

    tts = FakeTts()
    v, tl, _ = mk(turn=slow, tts=tts, voice_filler_after_ms=150)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await until(lambda: n_of(tl, "audio_end") == 2)
    starts = msgs(tl, "audio_start")
    assert [bool(m.get("filler")) for m in starts] == [True, False] and {m["turn_id"] for m in starts} == {1}
    order = [m["type"] for m in msgs(tl) if m["type"] in ("audio_start", "audio_end", "reply")]
    assert order.index("reply") > order.index("audio_end")          # holding phrase fully played before the real answer starts
    assert tts.spoken[0] in sess_fillers() and "45,000" in tts.spoken[1] or "45" in tts.spoken[1]
    assert v.stats["fillers"] == 1 and v.stats["max_concurrent_graph"] == 1
    await v.close()


def sess_fillers():
    from app.voice.session import FILLERS
    return FILLERS


@pytest.mark.anyio
async def test_a_quick_answer_gets_no_holding_phrase():
    v, tl, _ = mk(voice_filler_after_ms=1500)
    await v.start()
    await v.submit_text("okay")
    await until(lambda: n_of(tl, "audio_end") == 1)
    assert not any(m.get("filler") for m in msgs(tl, "audio_start")) and v.stats.get("fillers", 0) == 0
    await v.close()


@pytest.mark.anyio
async def test_no_holding_phrase_when_it_is_switched_off_or_the_turn_went_stale():
    def slow(text):
        time.sleep(0.4)
        return {"reply": "An answer.", **OK}

    v, tl, _ = mk(turn=slow, voice_filler_after_ms=0)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await until(lambda: n_of(tl, "audio_end") == 1)
    assert v.stats.get("fillers", 0) == 0
    await v.close()
    v2, tl2, _ = mk(turn=slow, voice_filler_after_ms=100)
    await v2.start()
    await v2.submit_text("What is the fee for Python?")
    await v2.submit_text("Actually what is the duration?")          # the first turn is already stale: nothing is said for it
    await until(lambda: n_of(tl2, "reply") == 2 and not v2.speaking)
    assert all(m["turn_id"] == 2 for m in msgs(tl2, "audio_start"))
    await v2.close()


@pytest.mark.anyio
async def test_a_barge_in_cuts_the_holding_phrase_and_nothing_resumes():
    class SlowFiller(SlowTts):
        pass

    def slow(text):
        time.sleep(0.6)
        return {"reply": "An answer.", **OK}

    v, tl, _ = mk(turn=slow, tts=SlowFiller(), voice_filler_after_ms=100)
    await v.start()
    await v.submit_text("What is the fee for Python?")
    await until(lambda: len(frames(tl)) >= 2)
    await v.submit_text("What is the duration of Java?")
    await until(lambda: n_of(tl, "reply") == 2 and not v.speaking, timeout=8)
    end1 = next(i for i, (k, m) in enumerate(tl) if k == "json" and m["type"] == "audio_end" and m["turn_id"] == 1 and m.get("interrupted"))
    assert not any(k == "audio" and m["turn_id"] == 1 for k, m in tl[end1 + 1:])
    await v.close()


@pytest.mark.anyio
async def test_latency_from_the_end_of_speech_to_first_audio_is_measured():
    v, tl, _ = mk(stt=FakeStt(["What is the fee for Python?"]))
    await v.start()
    await feed(v, utterance())
    await until(lambda: n_of(tl, "audio_end") == 1)
    end = msgs(tl, "audio_end")[0]["timing"]
    assert end["endpoint_to_first_audio_ms"] >= 0 and msgs(tl, "reply")[0]["timing"]["endpoint_to_reply_ms"] >= 0
    assert any(e["event"] == "latency" for e in v.events)
    await v.close()


@pytest.mark.anyio
async def test_paced_playback_leaves_no_stray_tasks_behind():
    """Regression: the drain wait used to leave two un-awaited Event.wait() tasks per response ("Task was destroyed but it is pending")."""
    class Short(FakeTts):
        def synthesize(self, text):
            self.spoken.append(text)
            yield b"\x01\x00" * (self.sample_rate // 5)

    before = set(asyncio.all_tasks())
    v, tl, _ = mk(tts=Short(), voice_pace_audio=True, voice_pace_lead_ms=200)
    await v.start()
    for _ in range(3):
        await v.submit_text("okay")
        await until(lambda: not v.speaking and n_of(tl, "audio_end") >= 1)
        v.playback_ended(1)
    await v.submit_text("thanks")
    await until(lambda: n_of(tl, "audio_end") == 4, timeout=8)
    await asyncio.sleep(0.05)
    stray = [t for t in asyncio.all_tasks() if t not in before and not t.done() and t.get_name() != "voice-runner"]
    assert stray == []
    await v.close()


@pytest.mark.anyio
async def test_the_per_node_timings_reach_the_reply_and_the_events():
    v, tl, _ = mk(turn=lambda text: {"reply": "ok.", **OK, "node_ms": {"triage": 1400, "knowledge": 4500}})
    await v.start()
    await v.submit_text("What is the fee?")
    await until(lambda: n_of(tl, "reply") == 1)
    assert msgs(tl, "reply")[0]["timing"]["nodes"] == {"triage": 1400, "knowledge": 4500}
    assert any(e["event"] == "graph_done" and e.get("nodes") for e in v.events)
    await v.close()
