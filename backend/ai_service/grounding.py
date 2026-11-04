"""Steps must come from the knowledge base.

A step the model writes (a numbered or bulleted line in a chat answer, a
technician's suggested next step) is kept only when the KB text it was
given supports it:
- every menu path in it ("Admin > Applications > APM") appears in the KB
  text;
- every hostname, port or number in it appears in the KB text (no
  invented "plus.site24x7.com on 443" or "500 a day");
- most of its content words (STEP_SUPPORT) appear in the KB text.

Deterministic and conservative: a step the KB doesn't back is dropped,
never rewritten.
"""

from __future__ import annotations

import re

from backend.ai_service.schemas import KBCandidate

STEP_SUPPORT = 0.6
_WORD = re.compile(r"[a-z0-9]+")
_STEP_LINE = re.compile(r"^(\s*)(\d+[.)]|[-*•])(\s+)(.*)$")
_MENU_PATH = re.compile(r"[A-Za-z][\w&'/ ]*?(?:\s*(?:>|→)\s*[A-Za-z][\w&'/ ]*)+")
_HOSTNAME = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\.(?:com|net|org|io|in|eu|au|cn|jp|ca|uk)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_COMMON = frozenset("""
    a an the and or but if then than to of in on at by for with from into onto as is are be been
    it its this that these those you your yours we our they their them there here can could
    should would will may might must do does did done make sure check go open click select choose
    use using want need please also any all each every some more most other such only just
    site24x7 step steps first next then finally again sure
""".split())


def kb_text(candidates: list[KBCandidate]) -> str:
    return "\n".join(f"{c.instruction}\n{c.response}" for c in candidates)


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    """Stemmed words of `text` that carry meaning (no filler, no short words)."""
    return {_stem(w) for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _COMMON}



def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _path_in(path: str, kb_norm: str) -> bool:
    """The menu path appears in the KB text. The first and last parts may
    carry surrounding words ("Go to Admin", "APM and click Add"), so any
    tail of the first part and any head of the last part will do."""
    parts = [p.strip().lower() for p in re.split(r"\s*(?:>|→)\s*", path) if p.strip()]
    first, last, middle = parts[0].split(), parts[-1].split(), parts[1:-1]
    for i in range(len(first)):
        for j in range(len(last), 0, -1):
            seq = [" ".join(first[i:]), *middle, " ".join(last[:j])]
            if re.search(r"\s*(?:>|→)\s*".join(re.escape(p) for p in seq), kb_norm):
                return True
    return False


def supported(step: str, kb: str) -> bool:
    """True when the KB text `kb` backs `step` (see the module docstring)."""
    if not kb.strip():
        return False
    kb_norm = _normal(kb)
    if any(not _path_in(path, kb_norm) for path in _MENU_PATH.findall(step)):
        return False
    for host in _HOSTNAME.findall(step):
        if host.lower() not in kb_norm:
            return False
    kb_numbers = set(_NUMBER.findall(kb))
    if any(n not in kb_numbers for n in _NUMBER.findall(step)):
        return False
    words = content_words(step)
    if not words:
        return True
    return len(words & content_words(kb)) / len(words) >= STEP_SUPPORT


def ground_steps(text: str, kb: str) -> tuple[str, int]:
    """`text` without its step lines (numbered or bulleted) that the KB
    doesn't support; numbered steps are renumbered. Returns the text and
    how many steps were dropped."""
    out, dropped, number = [], 0, 0
    for line in text.splitlines():
        match = _STEP_LINE.match(line)
        if not match:
            out.append(line)
            continue
        indent, marker, space, body = match.groups()
        if not supported(body, kb):
            dropped += 1
            continue
        if marker[0].isdigit():
            number += 1
            marker = f"{number}{marker[-1]}"
        out.append(f"{indent}{marker}{space}{body}")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip(), dropped


def grounded_steps(steps: list[str], kb: str) -> list[str]:
    """The suggested steps the KB text supports."""
    return [s for s in steps if supported(s, kb)]
