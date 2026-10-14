"""clean_email — strip quoted reply chains, signatures, and forwarded
headers out of an email-channel ticket before it reaches the graph.

Run as a pre-step in the /tickets/new handler, not inside the graph —
it's text hygiene, not a routing/classification decision.
"""

from __future__ import annotations

import re

_QUOTED_LINE_RE = re.compile(r"^\s*>.*$", re.MULTILINE)
_ON_WROTE_RE = re.compile(
    r"^On .{0,120}? wrote:\s*$.*", re.MULTILINE | re.DOTALL
)
_FORWARDED_HEADER_RE = re.compile(
    r"^-{2,}\s*Forwarded message\s*-{2,}.*", re.MULTILINE | re.DOTALL | re.IGNORECASE
)
_HEADER_BLOCK_RE = re.compile(
    r"^(From|Sent|To|Subject|Date):.*$", re.MULTILINE | re.IGNORECASE
)
_SIGNATURE_RE = re.compile(r"^--\s*$.*", re.MULTILINE | re.DOTALL)


def clean_email(raw_text: str) -> str:
    text = raw_text

    # Cut everything from a forwarded-message marker or an "On ... wrote:"
    # quote header onward — both mark the start of prior-thread content.
    text = _FORWARDED_HEADER_RE.sub("", text)
    text = _ON_WROTE_RE.sub("", text)

    # Drop leftover header lines (From:/Sent:/To:/Subject:/Date:) that
    # weren't part of a caught forwarded block, and any '>' quoted lines.
    text = _HEADER_BLOCK_RE.sub("", text)
    text = _QUOTED_LINE_RE.sub("", text)

    # Cut a trailing signature block (a lone "--" line onward).
    text = _SIGNATURE_RE.sub("", text)

    # Collapse the blank-line debris left behind by the stripping above.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
