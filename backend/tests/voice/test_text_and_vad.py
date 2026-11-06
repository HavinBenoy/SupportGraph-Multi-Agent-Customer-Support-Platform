import pytest

from app.voice.text import sentences, speakable
from app.voice.vad import FRAME, FRAME_MS, EnergyVad, Endpointer
from tests.voice.audio import RATE, chunks, silence, tone, utterance


# ---- speakable / sentences --------------------------------------------------------------------------------------------------
def test_speakable_removes_citations_markdown_and_links():
    t = speakable("The fee is **60%** at registration [fees-and-payments#2]. See https://example.com/x for more.")
    assert "[" not in t and "**" not in t and "https" not in t and "60%" in t


def test_speakable_spells_batch_and_reference_ids():
    t = speakable("Your batch PYF-101 is fine. Reference ESC-64ED856.")
    assert "P Y F 1 0 1" in t and "E S C dash 6 4 E D 8 5 6" in t


def test_speakable_drops_the_sample_data_notice_but_not_the_written_reply():
    reply = "Fees are 60% [fees#1]. (Demo data: this answer comes from sample documents, not the institute's real policy.)"
    assert "Demo data" not in speakable(reply) and "Demo data" in reply


def test_speakable_caps_length_at_a_sentence_and_points_to_the_chat():
    long = " ".join(f"Sentence number {i} is here." for i in range(80))
    t = speakable(long, max_chars=200)
    assert len(t) < 260 and t.endswith("The rest is in the chat.") and "number 3 is here. " in t


def test_sentences_split():
    assert sentences("One. Two! Three?\nFour") == ["One.", "Two!", "Three?", "Four"]
    assert speakable("") == ""


# ---- endpointer with a scripted VAD ----------------------------------------------------------------------------------------
class Scripted:
    def __init__(self, probs):
        self.probs, self.i = list(probs), 0

    def reset(self):
        self.i = 0

    def prob(self, frame):
        p = self.probs[min(self.i, len(self.probs) - 1)]
        self.i += 1
        return p


def run(probs, **kw):
    ep = Endpointer(Scripted(probs), **kw)
    pcm = b"\x01\x00" * (FRAME * len(probs))
    out = []
    for c in chunks(pcm, 1000):  # odd chunk sizes exercise the re-framing
        out += ep.feed(c)
    return out


def test_speech_then_silence_gives_start_then_one_utterance():
    ev = run([0.0] * 5 + [0.9] * 30 + [0.0] * 30)
    assert [e.kind for e in ev] == ["speech_start", "utterance"]
    assert ev[1].ms >= 900 and len(ev[1].audio) > 0


def test_a_single_noisy_frame_does_not_start_speech():
    assert run([0.0] * 5 + [0.9] + [0.0] * 40) == []


def test_pre_roll_keeps_audio_from_before_speech_was_confirmed():
    ev = run([0.0] * 20 + [0.9] * 30 + [0.0] * 30)
    utt = ev[1]
    assert len(utt.audio) // 2 * 1000 // RATE > 30 * FRAME_MS  # more than the speech frames alone


def test_short_blip_is_reported_as_too_short():
    ev = run([0.9] * 4 + [0.0] * 40, min_utterance_ms=500)
    assert [e.kind for e in ev] == ["speech_start", "too_short"]


def test_brief_dip_inside_speech_does_not_split_the_utterance():
    ev = run([0.9] * 10 + [0.1] * 5 + [0.9] * 10 + [0.0] * 30)
    assert [e.kind for e in ev] == ["speech_start", "utterance"]


def test_hysteresis_probability_between_end_and_start_thresholds_keeps_speech():
    ev = run([0.9] * 10 + [0.45] * 60)  # below 0.5 start threshold but above 0.35 end threshold: still speaking, never ends
    assert [e.kind for e in ev] == ["speech_start"]


def test_max_length_cuts_a_monologue():
    ev = run([0.9] * 300, max_utterance_s=3)
    first = [e for e in ev if e.kind == "utterance"][0]  # the cut; continuing speech then starts a new utterance
    assert 2.9 * RATE <= len(first.audio) // 2 <= 3 * RATE + FRAME


def test_two_utterances_in_a_row():
    ev = run(([0.0] * 3 + [0.9] * 25 + [0.0] * 30) * 2)
    assert [e.kind for e in ev] == ["speech_start", "utterance", "speech_start", "utterance"]


def test_energy_vad_endpointer_on_synthetic_audio():
    ep = Endpointer(EnergyVad())
    kinds = [e.kind for c in chunks(silence(300) + utterance(700, 900)) for e in ep.feed(c)]
    assert kinds == ["speech_start", "utterance"]
