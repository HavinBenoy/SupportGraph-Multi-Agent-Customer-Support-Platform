"""Fixed wording for the branches that do not call a model. Plain constants: easy to review and translate, and nothing a
user types can change them. None of them promises an action the system cannot perform yet (no "we will call you")."""

CLARIFY = {
    "injection_suspected": ("I can help with questions about courses, eligibility, fees, admissions and your own batch. "
                            "I can't follow instructions that change how I work or show internal information."),
    "other_person_data": ("I can't share another student's records or personal details. After signing in, I can show "
                          "you your own attendance, schedule and scores."),
    "chitchat": "You're welcome! Ask me anything else about courses, fees, admissions or your own batch.",
    "low_confidence": ("Could you tell me a bit more? For example, name the batch (like PYF-101) or the course you mean."),
    "sign_in_required": ("I can look up your own records only after you sign in as a student. I can still answer general "
                         "questions about courses, fees and admissions."),
}

ESCALATE = ("This needs a person from our support team to look into it, and I can't arrange that from this chat yet. "
            "Please contact the support team directly and mention your batch id if you have one.")

SAMPLE_NOTICE = "(Demo data: this answer comes from sample documents, not the institute's real policy.)"
