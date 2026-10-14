"""Shared text hygiene for the keyword/phrase detectors
(backend/closing_ack.py, backend/handoff_signals.py).

LLM output reliably uses typographic apostrophes ("I don't have
access"), and phone keyboards produce them for customer messages too,
so any pattern written with an ASCII `'` silently misses the exact
phrasing it exists to catch. Found the hard way: the live invoice
scenario escaped the limitation check purely because of the curly
apostrophe in "don't".
"""

from __future__ import annotations

_APOSTROPHES = str.maketrans(
    {"’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'"}
)


def normalize_apostrophes(text: str) -> str:
    """Fold every apostrophe variant to ASCII `'` so literal patterns
    match regardless of which one the writer (or model) used."""
    return text.translate(_APOSTROPHES)
