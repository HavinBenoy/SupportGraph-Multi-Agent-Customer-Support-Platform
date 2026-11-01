"""Tiny deterministic text normaliser shared by the offline fakes (hashing embedder, extractive FAQ model).

Not used with real models. It exists so that the offline demo and the tests behave sensibly without any model:
"Returns", "returned" and "return" must count as the same word.
"""

import re

_TOKEN = re.compile(r"\$?\d+(?:\.\d+)?|[a-z]+")

STOPWORDS = frozenset("""
a an the and or of to in on for with is are was were be been being do does did i you your yours my me we our us it its
this that these those at by from as can could will would should how what when where which who why not no if so but than
then there their they them have has had get got also any some such into out up over after before about just only very
more most much many""".split())


def stem(word: str) -> str:
    """Crude suffix stripping, applied identically to both sides of every comparison.
    shipping/shipped/ships -> ship, returns/returned -> return, cancellation/cancelled -> cancel."""
    if not word.isalpha() or len(word) <= 3:
        return word
    for suffix in ("ation", "ing", "ed", "es", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                break
            if suffix == "es" and not word[:-2].endswith(("s", "x", "z", "ch", "sh")):
                continue  # "shipes" is not a plural; plain "-s" handles "returns", "codes" etc.
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":  # shipp -> ship (keeps "cancell"->below)
        word = word[:-1]
    if word.endswith("ll") and len(word) > 4:  # cancell -> cancel
        word = word[:-1]
    if word.endswith("y") and len(word) > 5:  # delivery -> deliver, warranty -> warrant
        word = word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """Lowercased word and number tokens, markdown emphasis stripped, stopwords removed, stemmed."""
    out = []
    for raw in _TOKEN.findall(text.lower().replace("*", " ")):
        if raw in STOPWORDS:
            continue
        out.append(stem(raw))
    return out
