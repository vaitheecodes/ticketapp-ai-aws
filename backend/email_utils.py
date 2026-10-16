"""Email helpers.

- clean_email: strip quoted reply chains, signatures, and forwarded
  headers out of an email-channel ticket before it reaches the graph.
  Run as a pre-step in the /tickets/new handler, not inside the graph —
  it's text hygiene, not a routing/classification decision.
- format_email_reply: wrap an AI-written body in the fixed greeting /
  closing / signature used for ticket-type replies. Done in code rather
  than asked of the LLM so every reply has exactly the same frame.
- send_email: deliver a reply over SMTP when SMTP_HOST and SMTP_FROM are
  set; otherwise a silent no-op (the reply still lands in the ticket
  thread, which is the source of truth).
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SIGNATURE = "Regards,\nSite24x7 Support Team"
DEFAULT_CLOSING = "If anything is still unclear, just reply to this email and we'll pick it up."

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


def first_name(full_name: str | None) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else "there"


def format_email_reply(name: str | None, body: str, closing: str = DEFAULT_CLOSING) -> str:
    return f"Hi {first_name(name)},\n\n{body.strip()}\n\n{closing}\n\n{SIGNATURE}"


def email_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to: str, subject: str, body: str) -> bool:
    """Best effort: returns False (and logs) instead of raising, so a mail
    outage never fails the request that produced the reply."""
    if not email_configured():
        return False
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=15) as smtp:
            if os.environ.get("SMTP_USE_TLS", "true").lower() != "false":
                smtp.starttls()
            username = os.environ.get("SMTP_USERNAME")
            if username:
                smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("Failed to send ticket email to %s", to)
        return False
