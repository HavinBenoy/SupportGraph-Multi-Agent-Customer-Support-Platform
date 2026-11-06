"""VoiceSession logic with fake STT/TTS/VAD: pipeline order, barge-in, limits, failure handling. No sockets, no models."""

import asyncio

import pytest

from app.config import Settings
from app.voice import session as sess
from app.voice.session import SessionLimit, VoiceSession
from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from app.voice.vad import EnergyVad
from tests.voice.audio import RATE, chunks, silence, tone, utterance


def mk(turn=None, stt=None, tts=None, **kw):
    s = Settings(_env_file=None, voice_provider="fake", **kw)
    sent, audio = [], []

    async def sj(m):
        sent.append(m)

    async def sb(b):
        audio.append(b)

    calls = []

    def default_turn(text):
        calls.append(text)
        return {"reply": f"You said: {text}. Reference ESC-ABC123.", "status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t1"}

    v = VoiceSession(s=s, vad=EnergyVad(), stt=stt or FakeStt(["hello there"]), tts=tts or FakeTts(), run_turn=turn or default_turn,
                     send_json=sj, send_bytes=sb)
    return v, sent, audio, calls


async def feed(v, pcm):
    for c in chunks(pcm):
        await v.feed_audio(c)


async def until(sent, kind, timeout=3.0):
    async def wait():
        while not any(m["type"] == kind for m in sent):
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)


def kinds(sent):
    return [m["type"] for m in sent]


@pytest.mark.anyio
async def test_full_turn_audio_in_text_out_audio_out():
    v, sent, audio, calls = mk()
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_end")
    assert kinds(sent)[:7] == ["speech_start", "transcribing", "transcript", "transcript", "thinking", "reply", "audio_start"]
    assert [m["final"] for m in sent if m["type"] == "transcript"] == [False, True]
    assert calls == ["hello there"] and audio and kinds(sent)[-1] == "audio_end"
    reply = next(m for m in sent if m["type"] == "reply")
    assert "ESC-ABC123" in reply["reply"] and reply["timing"]["turn_ms"] >= 0 and "stt_ms" in reply["timing"]
    assert next(m for m in sent if m["type"] == "audio_start")["sample_rate"] == 22050
    await v.close()


@pytest.mark.anyio
async def test_speech_is_adapted_but_written_reply_is_not():
    tts = FakeTts()
    v, sent, audio, _ = mk(tts=tts)
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_end")
    assert any("E S C dash A B C 1 2 3" in s for s in tts.spoken)
    assert "ESC-ABC123" in next(m for m in sent if m["type"] == "reply")["reply"]
    await v.close()


@pytest.mark.anyio
async def test_noise_without_speech_does_nothing():
    v, sent, audio, calls = mk()
    await v.start()
    await feed(v, silence(1500))
    await asyncio.sleep(0.1)
    assert sent == [] and calls == []
    await v.close()


@pytest.mark.anyio
async def test_blip_shorter_than_minimum_is_ignored_politely():
    v, sent, audio, calls = mk()
    await v.start()
    await feed(v, tone(130) + silence(900))
    await asyncio.sleep(0.1)
    assert calls == [] and {"type": "notice", "detail": "too_short"} in sent
    await v.close()


@pytest.mark.anyio
async def test_empty_transcript_is_ignored_and_never_reaches_the_graph():
    v, sent, audio, calls = mk(stt=FakeStt([""]))
    await v.start()
    await feed(v, utterance())
    await until(sent, "transcript")
    await asyncio.sleep(0.1)
    assert calls == [] and next(m for m in sent if m["type"] == "transcript")["ignored"] is True and "thinking" not in kinds(sent)
    await v.close()


@pytest.mark.anyio
async def test_barge_in_stops_the_bot_and_the_caller_is_heard():
    gate = asyncio.Event()

    class SlowTts(FakeTts):
        def synthesize(self, text):
            for _ in range(40):
                yield b"\x01\x00" * 2000
                import time
                time.sleep(0.01)

    v, sent, audio, calls = mk(tts=SlowTts(), stt=FakeStt(["first question", "second question"]))
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_start")
    await asyncio.sleep(0.05)
    n_before = len(audio)
    await feed(v, tone(500) + silence(900))          # the caller talks over the bot
    await until(sent, "interrupted")
    await until(sent, "audio_end")
    end = [m for m in sent if m["type"] == "audio_end"][0]
    assert end["interrupted"] is True and len(audio) < n_before + 40
    await asyncio.wait_for(_until_calls(calls, 2), 5)
    assert calls[-1] == "second question"             # the interrupting speech became the next turn
    await v.close()


@pytest.mark.anyio
async def test_short_noise_while_the_bot_speaks_does_not_interrupt():
    class SlowTts(FakeTts):
        def synthesize(self, text):
            import time
            for _ in range(20):
                yield b"\x01\x00" * 1000
                time.sleep(0.01)

    v, sent, audio, calls = mk(tts=SlowTts(), voice_barge_in_ms=400)
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_start")
    await feed(v, tone(160) + silence(200))           # a cough: shorter than the barge-in threshold
    await until(sent, "audio_end")
    assert "interrupted" not in kinds(sent)
    await v.close()


@pytest.mark.anyio
async def test_stt_failure_is_reported_without_ending_the_call():
    class Broken:
        def transcribe(self, pcm, prompt=""):
            raise RuntimeError("model missing")

    v, sent, audio, calls = mk(stt=Broken())
    await v.start()
    await feed(v, utterance())
    await until(sent, "error")
    assert calls == [] and not next(m for m in sent if m["type"] == "error").get("fatal")
    await v.close()


@pytest.mark.anyio
async def test_tts_failure_still_delivers_the_text_reply():
    class BrokenTts(FakeTts):
        def synthesize(self, text):
            raise RuntimeError("voice missing")
            yield b""

    v, sent, audio, calls = mk(tts=BrokenTts())
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_end")
    assert "reply" in kinds(sent) and any(m["type"] == "error" for m in sent)
    await v.close()


@pytest.mark.anyio
async def test_graph_failure_does_not_kill_the_call_and_next_turn_works():
    state = {"n": 0}

    def flaky(text):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return {"reply": "ok", "status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t"}

    v, sent, audio, calls = mk(turn=flaky, stt=FakeStt(["a", "b"]))
    await v.start()
    await feed(v, utterance())
    await until(sent, "error")
    await feed(v, utterance())
    await until(sent, "audio_end")
    assert "reply" in kinds(sent)
    await v.close()


@pytest.mark.anyio
async def test_typed_text_uses_the_same_path():
    v, sent, audio, calls = mk()
    await v.start()
    await v.submit_text("what are the fees")
    await until(sent, "audio_end")
    assert calls == ["what are the fees"]
    await v.close()


@pytest.mark.anyio
async def test_turn_limit_ends_the_call():
    v, sent, audio, calls = mk(voice_max_turns=1)
    await v.start()
    await v.submit_text("one")
    await until(sent, "audio_end")
    with pytest.raises(SessionLimit, match="turn limit"):
        await v.submit_text("two")
    await v.close()


@pytest.mark.anyio
async def test_oversized_audio_message_is_refused():
    v, *_ = mk()
    with pytest.raises(SessionLimit):
        await v.feed_audio(b"\x00" * (sess.MAX_FRAME_BYTES + 2))


@pytest.mark.anyio
async def test_audio_faster_than_real_time_is_refused():
    v, *_ = mk()
    with pytest.raises(SessionLimit, match="faster than real time"):
        for c in chunks(silence(20000), 32000):
            await v.feed_audio(c)


@pytest.mark.anyio
async def test_session_time_limit(monkeypatch):
    v, *_ = mk(voice_max_session_s=30)
    v._started -= 31
    with pytest.raises(SessionLimit, match="time limit"):
        await v.feed_audio(b"\x00\x00")


# ---- M2.1: ending the call after a goodbye ---------------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_goodbye_is_spoken_before_the_call_ends():
    ended = []

    def turn(text):
        return {"reply": "Thanks for getting in touch. Goodbye!", "status": "answered", "citations": [], "escalation_id": None,
                "ticket_id": "t1", "end_call": True}

    v, sent, audio, _ = mk(turn=turn, stt=FakeStt(["that's all, bye"]))

    async def on_end():
        ended.append(kinds(sent)[:])

    v.on_end = on_end
    await v.start()
    await feed(v, utterance())
    await asyncio.wait_for(v._worker, 5)             # the worker returns after the goodbye
    ks = kinds(sent)
    assert ks.index("audio_end") < ks.index("call_end") and ks[-1] == "call_end"
    assert audio and ended and "audio_end" in ended[0]  # the socket is closed only after the closing was spoken
    assert next(m for m in sent if m["type"] == "call_end")["reason"] == "goodbye"
    await v.close()


@pytest.mark.anyio
async def test_a_normal_turn_does_not_end_the_call():
    v, sent, _, _ = mk(stt=FakeStt(["hello there"]))
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_end")
    assert "call_end" not in kinds(sent) and not v._worker.done()
    await v.close()


@pytest.mark.anyio
async def test_barge_in_still_cancels_audio_and_the_next_utterance_is_handled():
    """The caller talks over the bot ("I am just testing"): playback stops, the utterance is transcribed and answered as usual."""
    replies_seen = []

    def turn(text):
        replies_seen.append(text)
        return {"reply": "First sentence of a long answer. " * 8, "status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t1"}

    tts = FakeTts()
    v, sent, audio, _ = mk(turn=turn, stt=FakeStt(["tell me about fees", "I am just testing"]), tts=tts)
    await v.start()
    await feed(v, utterance())
    await until(sent, "audio_start")
    await feed(v, utterance())                       # speech while the bot is speaking
    await until(sent, "interrupted")
    await asyncio.wait_for(_until_count(sent, "reply", 2), 5)
    assert replies_seen == ["tell me about fees", "I am just testing"]
    await v.close()


async def _until_count(sent, kind, n):
    while sum(m["type"] == kind for m in sent) < n:
        await asyncio.sleep(0.01)


async def _until_calls(calls, n):
    while len(calls) < n:
        await asyncio.sleep(0.01)
