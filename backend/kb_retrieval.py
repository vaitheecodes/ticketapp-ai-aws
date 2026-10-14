"""Lightweight KB-candidate retrieval for the live app.

Loads the 386 canonical Site24x7 KB entries (data/silver/tickets.parquet
+ resolutions.parquet, deduped by `kb_entry_id` — see
src/pipeline/02_silver_transform.py) once per process, and scores them
against incoming ticket text with TF-IDF-weighted cosine similarity over
word tokens. No embedding model is wired in yet (see CLAUDE.md's
src/agent/ eval-track roadmap); this is deliberately simple and
dependency-free, but it's enough to ground classify_ticket's confidence
and ask_clarifying_question's prompt in the real intent taxonomy instead
of asking an LLM to classify blind against a bare list of category
names it has never seen an example of.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TICKETS_PATH = _PROJECT_ROOT / "data" / "silver" / "tickets.parquet"
_RESOLUTIONS_PATH = _PROJECT_ROOT / "data" / "silver" / "resolutions.parquet"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Cosine-similarity score above which a top candidate is treated as a
# confident, direct KB match — strong enough to skip clarification and
# resolve straight to generate_response, or to boost classify_ticket's
# raw LLM confidence for a short/common phrasing it under-scored.
STRONG_MATCH_SCORE = 0.40

# The 386 KB instructions are formally phrased ("how do I...", "why is
# X..."); real customer messages are casual ("I want...", "can you
# help..."). Against this small, stylistically narrow corpus, generic
# filler words end up RARE in the KB text and so get an anomalously
# high document-frequency-based IDF (e.g. "want" appears in only 4 of
# 386 KB instructions), which lets filler-word overlap outweigh real
# topic words. Stripped before scoring rather than left to IDF, since
# IDF alone isn't reliable at this corpus size.
_STOPWORDS = frozenset(
    """
    i me my myself we our ours ourselves you your yours yourself
    yourselves he him his she her it its they them their this that
    these those am is are was were be been being have has had having
    do does did doing a an the and but if or because as until while
    of at by for with about against between into through during
    before after above below to from up down in out on off over under
    again further then once here there when where why how all any
    both each few more most other some such no nor not only own same
    so than too very s t can will just should now please want wanted
    wants needing need needs needed help helping helped would could
    like get got go going add adding yes ok okay thanks thank
    """.split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class KBEntry:
    kb_entry_id: str
    category: str
    intent: str
    instruction: str
    response: str


@dataclass(frozen=True)
class KBCandidate:
    entry: KBEntry
    score: float


class _KBIndex:
    def __init__(self, entries: list[KBEntry]):
        self.entries = entries
        self._doc_tokens = [_tokenize(e.instruction) for e in entries]

        doc_freq: Counter = Counter()
        for tokens in self._doc_tokens:
            doc_freq.update(set(tokens))
        n_docs = max(len(entries), 1)
        self._idf = {
            term: math.log((n_docs + 1) / (count + 1)) + 1.0
            for term, count in doc_freq.items()
        }
        self._doc_vectors = [self._vectorize(tokens) for tokens in self._doc_tokens]
        self._doc_norms = [
            math.sqrt(sum(v * v for v in vec.values())) for vec in self._doc_vectors
        ]

    def _vectorize(self, tokens: list[str]) -> Counter:
        tf = Counter(tokens)
        return Counter({t: c * self._idf.get(t, 1.0) for t, c in tf.items()})

    def search(self, text: str, top_k: int = 3) -> list[KBCandidate]:
        query_vec = self._vectorize(_tokenize(text))
        if not query_vec:
            return []
        query_norm = math.sqrt(sum(v * v for v in query_vec.values()))
        if query_norm == 0:
            return []

        scored = []
        for entry, doc_vec, doc_norm in zip(
            self.entries, self._doc_vectors, self._doc_norms
        ):
            if doc_norm == 0:
                continue
            dot = sum(w * doc_vec.get(t, 0.0) for t, w in query_vec.items())
            score = dot / (query_norm * doc_norm)
            if score > 0:
                scored.append(KBCandidate(entry=entry, score=score))

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]


@lru_cache(maxsize=1)
def _load_index() -> "_KBIndex | None":
    if not _TICKETS_PATH.exists() or not _RESOLUTIONS_PATH.exists():
        return None

    tickets = pd.read_parquet(
        _TICKETS_PATH,
        columns=["ticket_id", "kb_entry_id", "category", "intent", "instruction_text"],
    )
    resolutions = pd.read_parquet(
        _RESOLUTIONS_PATH, columns=["ticket_id", "response_text"]
    )
    merged = tickets.merge(resolutions, on="ticket_id", how="left")
    canonical = merged.drop_duplicates(subset="kb_entry_id", keep="first")

    entries = [
        KBEntry(
            kb_entry_id=row.kb_entry_id,
            category=row.category,
            intent=row.intent,
            instruction=row.instruction_text,
            response=row.response_text or "",
        )
        for row in canonical.itertuples(index=False)
    ]
    return _KBIndex(entries)


def retrieve_candidates(ticket_text: str, top_k: int = 3) -> list[KBCandidate]:
    """Top-k KB entries (by TF-IDF cosine similarity) for `ticket_text`.
    Returns [] if the silver tables haven't been generated yet or no
    token in the query matches anything in the KB — callers must treat
    an empty list as "no grounding available", not as an error."""
    index = _load_index()
    if index is None:
        return []
    return index.search(ticket_text, top_k=top_k)
