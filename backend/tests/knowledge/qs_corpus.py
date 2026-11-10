"""A realistic, SYNTHETIC QSpiders-style knowledge base for the recall and precision tests (Part 2 final). Nothing here is real data: names and numbers are invented.

The documents are written the way different people write them (module pages, brochures, FAQ pairs, tables, announcements, profiles, archives), so the same fact appears in many
shapes. `TRUTH` lists, per change description, the lines a perfect system would propose to change (with the exact replacement) and the lines that look related but must stay."""

from app.knowledge.impact.discover import Candidate

DOCS: dict[str, tuple[str, str, str]] = {
    # --- affected, in different shapes ------------------------------------------------------------------------------------------------------------
    "ml-module": ("Machine Learning Module", """# Machine Learning Module

## Overview
Machine Learning covers regression, classification, clustering and neural networks. It is the most popular module of the Data Science programme.

## Faculty
Machine Learning faculty is Jyothi sir.

## Duration
The Machine Learning module runs for 6 weeks.
""", "Courses/Data Science"),
    "ds-brochure": ("Data Science Brochure", """# Data Science Brochure

## Modules
- Python for Data Science: 4 weeks, taught by Ramesh sir
- **Machine Learning**: 6 weeks, taught by Jyothi sir
- Statistics: 3 weeks, taught by Kavya madam

## Fee
The Data Science programme fee is ₹55,000.
""", "Marketing"),
    "faculty-directory": ("Faculty Directory", """# Faculty Directory

## Data Science programme
| Module | Faculty | Experience |
|---|---|---|
| Python for Data Science | Ramesh sir | 8 years |
| Machine Learning | Jyothi sir | 9 years |
| Statistics | Jyothi sir | 9 years |
| Deep Learning | Anita madam | 7 years |
""", "Courses/Data Science"),
    "ml-faq": ("Machine Learning FAQ", """# Machine Learning FAQ

## Faculty
Q: Who teaches Machine Learning?
A: Jyothi sir.

Q: How long is the Machine Learning module?
A: Six weeks.
""", "Admissions"),
    "ml-paraphrases": ("Data Science Programme Guide", """# Data Science Programme Guide

## Who teaches what
Jyothi sir handles ML sessions.

The ML instructor for Data Science is Jyothi.

Jyothi is responsible for teaching the machine learning module.

Our experienced instructor conducts the machine learning module.

## Reach us
Call the Data Science helpdesk for any question about the programme.
""", "Courses/Data Science"),
    "announcements": ("Announcements", """# Announcements

## Weekend bootcamp
Mr. Jyothi will conduct the machine-learning bootcamp on weekends.

## Holiday
The institute is closed on public holidays.
""", "Marketing"),
    # --- must NOT change ---------------------------------------------------------------------------------------------------------------------------
    "jyothi-profile": ("Jyothi sir Profile", """# Jyothi sir Profile

## Courses taught
Jyothi sir also teaches Statistics for Data Science.

## Biography
Jyothi sir has twelve years of teaching experience.
""", "Faculty"),
    "faculty-history": ("Faculty History", """# Faculty History

## Previous faculty
Until 9 November 2025 the Machine Learning faculty was Jyothi sir.

## Earlier batches
Machine Learning faculty (2024): Jyothi sir.
""", "Archive"),
    "python-course": ("Python Full Stack", """# Python Full Stack

## Faculty
Python Full Stack faculty is Ramesh sir.

## Duration
The Python Full Stack course runs for 24 weeks.
""", "Courses/Python"),
    "ml-description": ("What is Machine Learning", """# What is Machine Learning

Machine Learning is the study of algorithms that improve with data. The module introduces models, training and evaluation.
""", "Courses/Data Science"),
    "ml-fees": ("Module Fees", """# Module Fees

## Add-on modules
| Module | Fee |
|---|---|
| Machine Learning | ₹12,000 |
| Deep Learning | ₹15,000 |
""", "Courses/Data Science"),
    "hostel": ("Hostel Rules", "# Hostel Rules\n\n## Curfew\nResidents must return by 10:00 PM.\n", "Campus"),
    # --- shared / contradictory / misspelt -------------------------------------------------------------------------------------------------------
    "ml-stats-line": ("Statistics and ML Plan", """# Statistics and ML Plan

## Teaching plan
Jyothi sir teaches Statistics and Machine Learning.
""", "Courses/Data Science"),
    "placement-guide": ("Placement Preparation Guide", """# Placement Preparation Guide

## Mentors
Machine Learning faculty is Ramesh sir.
""", "Placements"),
    "typo-doc": ("Weekend Sessions", """# Weekend Sessions

## Machine Learning
Jyoti sir teaches machine learning on Saturdays.
""", "Courses/Data Science"),
}

# change description -> (lines that must be proposed with their replacement, lines that must NOT be edited automatically)
NEW = "Santhosh sir"
EDITS = {  # (doc, original line) -> replacement line for the main change
    ("ml-module", "Machine Learning faculty is Jyothi sir."): "Machine Learning faculty is Santhosh sir.",
    ("ds-brochure", "- **Machine Learning**: 6 weeks, taught by Jyothi sir"): "- **Machine Learning**: 6 weeks, taught by Santhosh sir",
    ("faculty-directory", "| Machine Learning | Jyothi sir | 9 years |"): "| Machine Learning | Santhosh sir | 9 years |",
    ("ml-faq", "A: Jyothi sir."): "A: Santhosh sir.",
    ("ml-paraphrases", "Jyothi sir handles ML sessions."): "Santhosh sir handles ML sessions.",
    ("ml-paraphrases", "The ML instructor for Data Science is Jyothi."): "The ML instructor for Data Science is Santhosh sir.",
    ("ml-paraphrases", "Jyothi is responsible for teaching the machine learning module."): "Santhosh sir is responsible for teaching the machine learning module.",
    ("announcements", "Mr. Jyothi will conduct the machine-learning bootcamp on weekends."): "Santhosh sir will conduct the machine-learning bootcamp on weekends.",
}
# lines that look related and must never be proposed as a change (a "potential" for a human is acceptable ONLY where listed in REVIEW)
KEEP = {
    ("faculty-directory", "| Statistics | Jyothi sir | 9 years |"), ("jyothi-profile", "Jyothi sir also teaches Statistics for Data Science."),
    ("jyothi-profile", "Jyothi sir has twelve years of teaching experience."), ("faculty-history", "Until 9 November 2025 the Machine Learning faculty was Jyothi sir."),
    ("faculty-history", "Machine Learning faculty (2024): Jyothi sir."), ("python-course", "Python Full Stack faculty is Ramesh sir."),
    ("ml-paraphrases", "Our experienced instructor conducts the machine learning module."), ("ds-brochure", "- Python for Data Science: 4 weeks, taught by Ramesh sir"),
    ("ds-brochure", "- Statistics: 3 weeks, taught by Kavya madam"),
}
# lines a person should look at: proposed at most as "potential" (never definite)
REVIEW = {("ml-stats-line", "Jyothi sir teaches Statistics and Machine Learning."), ("placement-guide", "Machine Learning faculty is Ramesh sir."),
          ("typo-doc", "Jyoti sir teaches machine learning on Saturdays.")}

OLD_KNOWN = "From today onwards, Machine Learning faculty is Santhosh sir instead of Jyothi sir."
NEW_ONLY = "From today onwards, Machine Learning faculty is Santhosh sir."
ABBREVIATED = "From today ML trainer is Santhosh sir."


def candidates() -> list[Candidate]:
    return [Candidate(doc_id=d, title=t, path=p, category="courses", tags=[], version=1, checksum="x", content=c) for d, (t, c, p) in DOCS.items()]
