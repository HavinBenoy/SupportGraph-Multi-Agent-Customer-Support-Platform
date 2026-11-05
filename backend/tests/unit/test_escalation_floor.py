import pytest

from app.guardrails.escalation_floor import escalation_floor, raise_priority


@pytest.mark.parametrize("text,category", [
    ("A person in the lab keeps following me and I'm scared to attend", "safety"),
    ("my senior is harassing me after class", "safety"),
    ("I feel unsafe at the centre", "safety"),
    ("He threatened me in the corridor", "safety"),
    ("I am afraid to come to class because of him", "safety"),
    ("If this is not fixed I will take this to court", "legal_threat"),
    ("I'll send a legal notice tomorrow", "legal_threat"),
    ("my lawyer will contact you", "legal_threat"),
    ("I was double charged on my card for registration", "payment_dispute"),
    ("The fee got deducted twice from my account", "payment_dispute"),
    ("duplicate payment was made for the second instalment", "payment_dispute"),
])
def test_serious_messages_are_caught(text, category):
    assert escalation_floor(text)[0] == category


@pytest.mark.parametrize("text", [
    "What is the fee for the Software Testing course?",
    "Can I pay in two instalments?",
    "Is the centre safe to reach by bus at night?",             # about travel, no alarm words
    "I'm afraid I will fail the quiz, any tips?",               # fear of failing is not a safety matter
    "How do I take this course again next year?",
    "Do you have a court yard or canteen at the centre?",
    "what is my attendance in PYF-101",
    "Please arrange a make-up class for the session I missed in PYD-301.",
    "I paid the first instalment, when is the second due?",
    "",
])
def test_ordinary_messages_do_not_fire(text):
    assert escalation_floor(text) is None


def test_priority_only_ever_rises():
    assert raise_priority("low", "urgent") == "urgent" and raise_priority("urgent", "high") == "urgent" and raise_priority("high", "high") == "high"
