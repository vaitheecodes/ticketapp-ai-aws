"""Lightweight KB-candidate retrieval for the live app.

Loads the 386 canonical Site24x7 KB entries once per process from
data/kb/kb_entries.json — exported from the silver Parquet tables
(tickets + resolutions, deduped by `kb_entry_id`) by scripts/export_kb.py,
so the deployed backend needs no pandas/pyarrow — and scores them
against incoming ticket text with TF-IDF-weighted cosine similarity over
word tokens. No embedding model is wired in yet (see CLAUDE.md's
src/agent/ eval-track roadmap); this is deliberately simple and
dependency-free, but it's enough to ground classify_ticket's confidence
and ask_clarifying_question's prompt in the real intent taxonomy instead
of asking an LLM to classify blind against a bare list of category
names it has never seen an example of.

Near-duplicates are collapsed into one topic: most of the 386 entries
are the same answer with a different question phrasing, capitalisation,
politeness preamble or filled-in monitor name (27 distinct topics). A
search scores every phrasing and returns each topic once, as its
best-matching entry, so the top results are distinct topics.

A best score below KB_MIN_SCORE (default 0.35) is not a match: nothing
is returned, and callers treat that as "no close KB match".

Monitor names the data pipeline filled into KB questions
("App-Server-04", "API-Gateway-EU") are not topic words: "I need to add
IOS app" matched three unrelated entries on "app" alone. They are left
out of the index. A how-to search (skip_faults) leaves out entries whose
question reports a fault, so "add an APM monitor" isn't answered from
"APM monitor is showing down".
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from backend import request_kind

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_PATH = _PROJECT_ROOT / "data" / "kb" / "kb_entries.json"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Cosine-similarity score above which a top candidate is treated as a
# confident, direct KB match — strong enough to skip clarification and
# resolve straight to generate_response, or to boost classify_ticket's
# raw LLM confidence for a short/common phrasing it under-scored.
STRONG_MATCH_SCORE = 0.40

# Below this a candidate is not a match at all (KB_MIN_SCORE overrides).
# "I see unable to connect to device error" scored 0.316 against "why was
# my device not discovered" on the word "device" alone.
DEFAULT_MIN_SCORE = 0.35

# Two entries of one category/intent whose answers are at least this
# similar (word-level) are one topic. Measured on the KB: variants differ
# by a politeness preamble or a filled-in monitor name (0.85-0.99).
DUPLICATE_ANSWER_SIMILARITY = 0.85


def kb_min_score() -> float:
    raw = os.environ.get("KB_MIN_SCORE", "").strip()
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        value = float(raw)
    except ValueError:
        logger.warning("KB_MIN_SCORE is not a number; using %.2f", DEFAULT_MIN_SCORE)
        return DEFAULT_MIN_SCORE
    return min(max(value, 0.0), 1.0)

# The 386 KB instructions are formally phrased ("how do I...", "why is
# X..."); real customer messages are casual ("I want...", "can you
# help..."). Against this small, stylistically narrow corpus, generic
# filler words end up RARE in the KB text and so get an anomalously
# high document-frequency-based IDF (e.g. "want" appears in only 4 of
# 386 KB instructions), which lets filler-word overlap outweigh real
# topic words. Stripped before scoring rather than left to IDF, since
# IDF alone isn't reliable at this corpus size. Task verbs ("add", "set"
# up) likewise: one approved "set up ..." question made "set" outweigh
# the topic of every "how do I set up X".
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
    like get got go going add adding set setup setting yes ok okay thanks thank
    """.split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


# A filled-in monitor name: two or more hyphenated parts, each starting
# with a capital letter or a digit ("App-Server-04", "K8s-Cluster-Prod").
# Lower-case words such as "third-party" are kept.
_MONITOR_NAME = re.compile(r"\b[A-Z0-9][A-Za-z0-9]*(?:-[A-Z0-9][A-Za-z0-9]*)+\b")


def _index_tokens(instruction: str) -> list[str]:
    return _tokenize(_MONITOR_NAME.sub(" ", instruction))


tokenize = _tokenize  # public: also used for incident lexical similarity


@dataclass(frozen=True)
class KBEntry:
    kb_entry_id: str
    category: str
    intent: str
    instruction: str
    response: str
    # data/kb/new_entries.json only: "escalate_with_info" entries tell the
    # customer what to collect and hand the case to support (graph.
    # generate_response); `collect` lists those details.
    handling: str = "answer"
    collect: tuple[str, ...] = ()
    # data/kb/new_entries.json only: "how-to" or "troubleshooting" (None:
    # guessed from the question's wording, request_kind.is_fault).
    kind: str | None = None

    @property
    def is_fault(self) -> bool:
        if self.kind:
            return self.kind == "troubleshooting"
        return request_kind.is_fault(self.instruction)


@dataclass(frozen=True)
class KBCandidate:
    entry: KBEntry
    score: float


def _answer_words(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def topic_ids(entries: list[KBEntry]) -> list[int]:
    """One topic number per entry: entries of the same category/intent
    whose answers are near-identical share a topic."""
    reps: list[tuple[tuple[str, str], list[str], int]] = []
    out = []
    for e in entries:
        words, key = _answer_words(e.response), (e.category, e.intent)
        for rep_key, rep_words, topic in reps:
            if rep_key == key and difflib.SequenceMatcher(
                    None, rep_words, words, autojunk=False).ratio() >= DUPLICATE_ANSWER_SIMILARITY:
                out.append(topic)
                break
        else:
            reps.append((key, words, len(reps)))
            out.append(len(reps) - 1)
    return out


class _KBIndex:
    def __init__(self, entries: list[KBEntry]):
        self.entries = entries
        self.topics = topic_ids(entries)
        self._doc_tokens = [_index_tokens(e.instruction) for e in entries]
        self._faults = [e.is_fault for e in entries]

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

    def search(self, text: str, top_k: int = 3, min_score: float = 0.0,
               category: str | None = None, skip_faults: bool = False) -> list[KBCandidate]:
        """Up to top_k distinct topics scoring at least min_score, each as
        its best-matching entry; only entries of `category` when given;
        no entry whose question reports a fault with skip_faults."""
        query_vec = self._vectorize(_tokenize(text))
        if not query_vec:
            return []
        query_norm = math.sqrt(sum(v * v for v in query_vec.values()))
        if query_norm == 0:
            return []

        best: dict[int, KBCandidate] = {}
        for entry, topic, doc_vec, doc_norm, fault in zip(
            self.entries, self.topics, self._doc_vectors, self._doc_norms, self._faults
        ):
            if doc_norm == 0 or (category is not None and entry.category != category) or (skip_faults and fault):
                continue
            dot = sum(w * doc_vec.get(t, 0.0) for t, w in query_vec.items())
            score = dot / (query_norm * doc_norm)
            if score > 0 and score >= min_score and (topic not in best or score > best[topic].score):
                best[topic] = KBCandidate(entry=entry, score=score)

        return sorted(best.values(), key=lambda c: c.score, reverse=True)[:top_k]


def build_index(entries: list[dict]) -> "_KBIndex":
    return _KBIndex([
        KBEntry(kb_entry_id=e["kb_entry_id"], category=e["category"], intent=e["intent"],
                instruction=e["instruction"], response=e["response"] or "",
                handling=e.get("handling") or "answer", collect=tuple(e.get("collect") or ()),
                kind=e.get("kind"))
        for e in entries
    ])


@lru_cache(maxsize=1)
def _load_index() -> "_KBIndex | None":
    """kb_entries.json plus the valid, non-example entries of
    data/kb/new_entries.json (see backend/kb_new_entries.py)."""
    if not KB_PATH.exists():
        return None
    from backend import kb_new_entries

    entries = json.loads(KB_PATH.read_text(encoding="utf-8"))["entries"]
    new = kb_new_entries.load(entries)
    if new.errors:
        logger.warning("data/kb/new_entries.json: %d problem(s), those entries are not used; "
                       "run scripts/validate_kb.py", len(new.errors))
    return build_index(entries + new.entries)


def entry_count() -> int:
    """KB entries the search uses (canonical + approved new); 0 when
    data/kb/kb_entries.json is missing or unreadable. Checked at startup
    (production refuses to start on 0) and shown on /api/meta."""
    try:
        index = _load_index()
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return len(index.entries) if index else 0


def retrieve_candidates(ticket_text: str, top_k: int = 3, min_score: float | None = None,
                        category: str | None = None, skip_faults: bool = False) -> list[KBCandidate]:
    """Top-k distinct KB topics (by TF-IDF cosine similarity) for
    `ticket_text`, none scoring below `min_score` (default KB_MIN_SCORE).
    Returns [] if data/kb/kb_entries.json hasn't been exported yet or
    nothing matches closely enough — callers must treat an empty list as
    "no close KB match": no grounding, not an error. `category` limits the
    search to that category's entries; `skip_faults` (a how-to request)
    leaves out entries whose question reports a fault."""
    index = _load_index()
    if index is None:
        return []
    floor = kb_min_score() if min_score is None else min_score
    return index.search(ticket_text, top_k=top_k, min_score=floor, category=category,
                        skip_faults=skip_faults)
