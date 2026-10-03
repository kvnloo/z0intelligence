"""Deterministic text normalisation shared by the corpus validator and the scorer."""
import re
import unicodedata

_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "′": "'",
           "–": "-", "—": "-", "−": "-", " ": " ", "　": " "}


def norm(text: str) -> str:
    """lowercase, NFKC, fold smart quotes/dashes, strip markdown emphasis/backticks, collapse ws."""
    t = unicodedata.normalize("NFKC", text or "")
    for k, v in _QUOTES.items():
        t = t.replace(k, v)
    t = t.replace("`", "").replace("*", "")
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t
