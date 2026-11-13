"""Finding a customer when the name was spelled or heard a little differently (Agarwal / Agrawal / Aggarwal / अग्रवाल).

A voice note comes back from speech-to-text in Devanagari, and shop names are written in Latin letters in many ways. Both are
reduced to a "skeleton": Devanagari is mapped to its consonants, Latin is lower-cased, vowels, doubled letters and common
look-alikes (v/w, f/ph, k/c/q, z/j, s/sh) are folded, and generic words (agencies, traders, store, Mr) are dropped. Two names
with the same skeleton, or nearly the same, are candidates. The tool never picks: it lists candidates for the model to confirm
with the owner when there is more than one.
"""
import re
from difflib import SequenceMatcher

GENERIC = {"agency", "agencies", "traders", "trader", "trading", "store", "stores", "enterprise", "enterprises", "mr", "mrs",
           "shri", "shree", "sri", "ji", "and", "co", "company", "pvt", "ltd", "private", "limited", "sons", "brothers", "bros",
           "mart", "shop", "the", "textiles", "textile", "hardware", "electricals", "electrical", "medical", "medicals", "sweets"}
# Devanagari consonants (and nukta forms) to Latin consonant letters; vowels and matras are dropped (the skeleton ignores them)
_DEV = {"क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n", "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n", "ट": "t", "ठ": "t",
        "ड": "d", "ढ": "d", "ण": "n", "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n", "प": "p", "फ": "f", "ब": "b", "भ": "b",
        "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "s", "ष": "s", "स": "s", "ह": "h", "ळ": "l", "क़": "k", "ख़": "k",
        "ग़": "g", "ज़": "j", "ड़": "d", "ढ़": "d", "फ़": "f", "य़": "y"}
_FOLD = (("ph", "f"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("bh", "b"),
         ("w", "v"), ("q", "k"), ("z", "j"), ("x", "ks"), ("y", ""), ("h", ""))


def _to_latin(text: str) -> str:
    return "".join(_DEV.get(ch, " " if "ऀ" <= ch <= "ॿ" and ch not in "।" and not ch.isalpha() else
                            ("" if "ऀ" <= ch <= "ॿ" else ch)) for ch in text)


def words(text: str) -> list[str]:
    text = _to_latin(str(text or "")).lower()
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in GENERIC]


def skeleton(text: str) -> str:
    """The name reduced to what survives spelling and hearing variants."""
    out = []
    for w in words(text):
        for a, b in _FOLD:
            w = w.replace(a, b)
        w = re.sub(r"[aeiou]", "", w)
        w = w.replace("c", "k")
        w = re.sub(r"(.)\1+", r"\1", w)
        if w:
            out.append(w)
    return " ".join(out)


def score(query: str, name: str) -> float:
    """0 to 1: how likely `name` is what the owner meant by `query`."""
    q, n = skeleton(query), skeleton(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qa, na = q.replace(" ", ""), n.replace(" ", "")
    if qa == na:
        return 0.98
    if min(len(qa), len(na)) >= 3 and (qa in na or na in qa):         # "agrawal" for "agrawal traders and sons"
        return 0.9
    return SequenceMatcher(None, qa, na).ratio()


def best(query: str, names: list[str], *, threshold: float = 0.8, limit: int = 5) -> list[tuple[str, float]]:
    """Candidates at or above the threshold, best first."""
    scored = sorted(((n, score(query, n)) for n in names), key=lambda t: (-t[1], t[0]))
    return [(n, round(s, 2)) for n, s in scored if s >= threshold][:limit]
