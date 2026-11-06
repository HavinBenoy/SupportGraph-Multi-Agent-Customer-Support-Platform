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


@pytest.mark.parametrize("text", [
    "I want to talk to a person, not a bot.",
    "I would like to speak to a human agent please",
    "Can I speak with someone about my enrolment?",
    "connect me to a real person",
    "please transfer me to an agent",
    "are you a bot? I want a real person",
    "I want to talk to a person about putting my enrolment on hold.",
])
def test_explicit_requests_for_a_person_are_caught(text):
    assert escalation_floor(text)[0] == "human_request"


@pytest.mark.parametrize("text", [
    "What is the Human Resources management course fee?",
    "A person can enrol online, right?",
    "I want to talk about the fee structure",
    "Can I speak to my trainer about the assignment?",
    "Can I talk to someone who studied here? (alumni)",
    "Is the person at the front desk available on Sunday?",
    "Are the classes taught by a human or recorded?",
])
def test_look_alikes_for_human_request_do_not_fire(text):
    assert escalation_floor(text) is None or escalation_floor(text)[0] != "human_request"


def test_human_request_floor_does_not_raise_priority():
    assert escalation_floor("I want to talk to a person")[1] == "low"


# M2.1: spoken, imperfectly transcribed requests for a person
@pytest.mark.parametrize("text", [
    "Please forward this call towards your supporting.",         # the real test call
    "forward my call to the support executive",
    "can you transfer the call to support?",
    "please put me through to the support team",
    "connect me with a support representative",
    "connect this call with a human",
    "redirect me to customer support",
    "I need to contact support",
    "please contact support for me",
    "I want to speak to someone",
    "let me talk to your manager",
    "talk to a counsellor please",
    "get me a human",
    "I need a real person",
    "human please",
    "customer care",
    "agent",
    "I don't want to talk to a bot",
])
def test_spoken_human_support_requests_are_caught(text):
    assert escalation_floor(text)[0] == "human_request"


@pytest.mark.parametrize("text", [
    "How can I contact the support team?",
    "What support do you offer after the course?",
    "Do you provide placement support?",
    "I want to talk to someone who teaches Java",
    "Can I talk to my trainer about the syllabus?",
    "How do I transfer my batch?",
    "transfer my fee to the next batch",
    "Forward the fee receipt",
    "connect to the wifi in the lab",
    "How do I contact my trainer?",
    "Can I pass the course with 60 percent?",
    "Okay, thank you. That's it. You can stop the call.",
    "hello",
])
def test_look_alikes_do_not_ask_for_a_person(text):
    assert escalation_floor(text) is None
