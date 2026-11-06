"""Fixed wording for the branches that do not call a model. Plain constants: easy to review and translate, and nothing a
user types can change them. None of them promises an action the system cannot perform yet (no "we will call you")."""

CLARIFY = {
    "injection_suspected": ("I can help with questions about courses, eligibility, fees, admissions and your own batch. "
                            "I can't follow instructions that change how I work or show internal information."),
    "other_person_data": ("I can't share another student's records or personal details. After signing in, I can show "
                          "you your own attendance, schedule and scores."),
    "chitchat": "Happy to help. Ask me anything about courses, fees, admissions or your own batch.",
    "low_confidence": ("Could you tell me a bit more? For example, name the batch (like PYF-101) or the course you mean."),
    "sign_in_required": ("I can look up your own records only after you sign in as a student. I can still answer general "
                         "questions about courses, fees and admissions."),
}

# Escalation (build step 4): a brief now exists and a team can see it, but nothing promises WHEN or WHAT. Staff-side SLA targets
# are internal and never shown here.
ESCALATE_SIGNED_IN = ("This needs a person on our {team} team. I've passed it to them with the details, so you shouldn't need to "
                      "repeat yourself (reference {ref}). I can't tell you when they will reply.")
ESCALATE_ANONYMOUS = ("This needs a person on our {team} team, and I've left them a note (reference {ref}). Because you are not "
                      "signed in they have no way to reach you, so please contact the support team directly and quote the reference.")
# An explicit request for a person (spoken or typed). Honest about what exists today: a note for the team, not a live transfer.
HUMAN_SIGNED_IN = ("Of course. I can't connect you to a person during this conversation yet, so I've passed your request to our {team} team "
                   "with the details (reference {ref}). You shouldn't need to repeat yourself, and I can't tell you when they will reply.")
HUMAN_ANONYMOUS = ("Of course. I can't connect you to a person during this conversation yet, but I've left a note for our {team} team "
                   "(reference {ref}). Because you are not signed in they have no way to reach you, so please contact the support team "
                   "directly and quote the reference.")

ESCALATE_UNRECORDED = ("This needs a person on our support team, but I couldn't record it just now. Please contact the support "
                       "team directly and mention your batch id if you have one.")

# Write branch (build step 4)
WRITE_REFUSALS = {
    "unclear": "I can request a make-up class or update your phone number. Tell me which, for example: \"make-up class for PYF-101\".",
    "missing_batch": "Which batch is it for? Please give the batch id, for example PYF-101.",
    "batch_not_found": "I couldn't find that batch on your account, so I can't make a request for it.",
    "no_missed_session": "I don't see a session you missed in the last {days} days for that batch, so there is nothing to make up.",
    "which_session": "You missed more than one session recently: {options}. Which date do you want a make-up class for?",
    "not_a_missed_session": "I can only request a make-up for a session you missed in the last {days} days. Your missed sessions: {options}.",
    "missing_phone": "What is the new phone number? Please include the full number.",
    "other_person": "I can only make requests for your own account, so I can't do that for someone else. Tell me what you want changed on your own record.",
    "bad_phone": "That doesn't look like a valid phone number. Please give 10 to 13 digits, with an optional leading +.",
}
MAKEUP_SUBMITTED = ("I've submitted a make-up class request for {batch} on {date} (reference {ref}). A staff member has to approve "
                    "it, and I can't promise the outcome or the date.")
MAKEUP_DUPLICATE = "You already have a pending make-up request for {batch} on {date} (reference {ref}); staff will review it."
PHONE_CONFIRM = "I'll change the phone number on your record to {phone}. Reply \"yes\" to confirm or \"no\" to cancel."
OUTCOMES = {
    ("request_makeup_class", "approved"): "Good news: your make-up class request {ref} was approved by our staff.",
    ("request_makeup_class", "rejected"): "Your make-up class request {ref} was not approved. Please contact the support team if you want to discuss it.",
    ("update_contact_phone", "approved"): "Done: your phone number is now {phone}.",
    ("update_contact_phone", "rejected"): "Okay, I haven't changed your phone number.",
}
AWAITING_STAFF = "Your request is still waiting for a staff decision. I'll show the outcome here once it is made."
AWAITING_CONFIRM = "I'm waiting for your answer: reply \"yes\" to confirm the change or \"no\" to cancel it."

# QA loop (build step 4): shown instead of a reply that failed the checks and could not be repaired.
QA_FALLBACK = ("I'm not able to give a reliable answer to that here. Please contact the support team directly, or ask in a "
               "different way.")

# The language model could not answer (down, timed out, daily budget reached). Fixed wording; never guesses an answer.
MODEL_UNAVAILABLE = ("I can't answer that right now because my language model isn't responding. Please try again in a "
                     "moment, or contact the support team directly.")

SAMPLE_NOTICE = "(Demo data: this answer comes from sample documents, not the institute's real policy.)"

# Conversation-control intents (app/conversation/intents.py): fixed, short, warm and professional. They promise nothing and reveal
# nothing, so they are safe for anonymous callers. Several variants per intent avoid sounding like a recording; the variant is picked
# from how many replies the conversation already has, so it is deterministic for tests and varies within a call.
CONVERSE = {
    "greeting": ["Hi there! How can I help you today?", "Hello! What can I help you with?", "Hi! How can I help?"],
    "thanks": ["You're welcome! Is there anything else I can help with?", "Happy to help. Anything else you'd like to know?",
               "My pleasure. Is there anything else?"],
    "acknowledgement": ["Okay. Is there anything else I can help with?", "Sure. Let me know if you need anything else.",
                        "Alright. I'm here if you have another question."],
    # a bare "yes" / "of course" / "go ahead" with nothing pending (a pending confirmation or approval is resolved before the graph)
    "affirm": ["Okay. What would you like to know?", "Sure. Go ahead, I'm listening.", "Alright. What can I help you with?"],
    "goodbye": ["You're welcome. Thanks for getting in touch. Take care, goodbye!",
                "Thanks for getting in touch. Have a good day, goodbye!"],
    "interruption": ["No problem. Ask me whenever you're ready.", "That's fine. What would you like to know?",
                     "No worries. Go ahead whenever you're ready."],
    # the caller says the assistant said or asked something unnecessary / not what they asked
    "correction": ["Sorry about that. What would you like to ask?", "My apologies. Go ahead, I'm listening.",
                   "Sorry, my mistake. How can I help?"],
}
GOODBYE_WHILE_PENDING = ("Okay, goodbye. Your request stays with our staff, and you'll see the outcome in this conversation "
                         "when you come back.")
