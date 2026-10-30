"""chat_response: what the customer reads.

- answer: a KB-grounded reply to the request (chat or email format).
- clarify: one targeted question naming the real candidate topics.
- is_continuation: does a follow-up refer back to the AI's last answer?

Email-format replies (ticket-type requests) are body-only from the model
and wrapped here with the fixed greeting/closing/signature, so every
email has the same frame. Chat replies are a live conversation: no
greeting, sign-off or signature, and clean_chat_reply removes any the
model writes anyway.
"""

from __future__ import annotations

import re

from backend import email_utils
from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES, history_text, kb_guidance_text
from backend.ai_service.schemas import (
    AnswerInput,
    ChatReply,
    ClarifyInput,
    ContinuationInput,
    ContinuationVerdict,
)

_EMAIL_BODY_NOTE = (
    " This reply is sent as an email. Write ONLY the body: no greeting, no "
    "sign-off, no signature, no subject line — those are added for you."
)


_CHAT_STYLE = (
    "This is a live chat, not an email. Write the way a support engineer types "
    "in chat: conversational, short (usually two to five sentences), straight to "
    "the point. No greeting, no sign-off, no signature, no subject line. Never "
    "write placeholder text in square brackets such as [Your Name] or [Customer "
    "Name], and never sign with a name or team."
)

# Chat output filter. A greeting line: "Hello," / "Hi Dana!" / "Dear customer,".
_GREETING_LINE = re.compile(
    r"^\s*(hello|hi|hey|dear|greetings|good (morning|afternoon|evening))\b\s*,?"
    r"(\s+[\w'.-]+){0,3}\s*[,!.:]?\s*$",
    re.IGNORECASE)
# A sign-off line; it and everything after it (the signature) is dropped.
_SIGN_OFF_LINE = re.compile(
    r"^\s*((best|kind|warm|warmest)( regards)?|regards|sincerely|cheers|"
    r"thanks|thank you|many thanks|all the best)\s*[,!.]?\s*$",
    re.IGNORECASE)
# A signature line on its own: "Site24x7 Support Team", "Customer Support".
_SIGNATURE_LINE = re.compile(
    r"^\s*(the )?(site24x7 )?(customer )?(support|technical support|help ?desk)( team)?\s*$",
    re.IGNORECASE)
# [Your Name], [Customer's Name], [insert link]: words only, so a UI path
# like [Admin > Monitors] or a markdown link [docs](url) is kept.
_PLACEHOLDER = re.compile(r"\[[A-Za-z][A-Za-z' -]{0,40}\](?!\()")


def clean_chat_reply(text: str) -> str:
    """Strip greeting lines, the sign-off and signature, and [Placeholder]
    text from a chat reply. A reply with nothing left (only placeholders
    and pleasantries) is an AI failure."""
    lines = _PLACEHOLDER.sub("", text).splitlines()
    while lines and (not lines[0].strip() or _GREETING_LINE.match(lines[0])):
        lines.pop(0)
    for i, line in enumerate(lines):
        if _SIGN_OFF_LINE.match(line) and i > 0:
            lines = lines[:i]
            break
    while lines and (not lines[-1].strip() or _SIGNATURE_LINE.match(lines[-1])):
        lines.pop()
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in lines)).strip()
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    pleasantries = (_GREETING_LINE, _SIGN_OFF_LINE, _SIGNATURE_LINE)
    if not any(re.search(r"[A-Za-z0-9]", line) and not any(p.match(line) for p in pleasantries)
               for line in cleaned.splitlines()):
        raise provider.AIError("AI chat reply was only placeholder text")
    return cleaned


def _frame(text: str, reply_format: str, customer_name: str | None) -> ChatReply:
    if reply_format == "email":
        text = email_utils.format_email_reply(customer_name, text)
    else:
        text = clean_chat_reply(text)
    return ChatReply(text=text)


def answer(data: AnswerInput) -> ChatReply:
    continuation_note = (
        " The ticket block is the conversation so far, and the customer's latest "
        "message follows up on your own most recent reply (asking you to elaborate, "
        "clarify, or compare something you said): answer that latest message "
        "directly, building on your prior reply, not as an isolated new request."
        if data.continuation
        else ""
    )
    if data.reply_format == "email":
        role = (
            "You are a helpful customer support agent for Site24x7 (an infrastructure "
            "monitoring product). Write a clear, concise, friendly reply that resolves "
            f"the customer's request.{continuation_note}{_EMAIL_BODY_NOTE}"
        )
    else:
        role = (
            "You are the Site24x7 support assistant (Site24x7 is an infrastructure "
            "monitoring product), chatting live with a customer. "
            f"{_CHAT_STYLE} Give numbered steps only when the customer has to do "
            "something, and keep each step to one line. If the request is too vague "
            "to answer well, ask exactly one short clarifying question instead of "
            f"guessing.{continuation_note}"
        )
    text = provider.complete_text(
        f"{role}\n\n"
        "Base the answer on the kb_guidance block, personalised to the customer's "
        "own wording; don't contradict it. Use the order_record block as the only "
        "source of truth for order facts. If answering would need a specific fact "
        "you weren't given (an order status, account detail, setting value), say "
        "plainly that you don't have access to it and ask for it, or say a person "
        f"will need to check — never guess. {GROUNDING_RULES}",
        {
            "category": data.category or "unknown",
            "customer_history": history_text(data.history),
            "kb_guidance": kb_guidance_text(data.kb_candidates),
            "order_record": data.order_context or "(none)",
            "ticket": data.ticket_text,
        },
    )
    return _frame(text, data.reply_format, data.customer_name)


def clarify(data: ClarifyInput) -> ChatReply:
    if data.kb_candidates:
        topics = "\n".join(
            f'{i + 1}. [{c.category} / {c.intent}]: e.g. "{c.instruction}"'
            for i, c in enumerate(data.kb_candidates[:3])
        )
        grounding = (
            "The candidate_topics block lists the closest real Site24x7 support "
            "topics this ticket could match, ranked by relevance. Ask exactly ONE "
            "short question that would tell you which of THESE SPECIFIC topics "
            "applies (or that none of them do). Refer to what they're actually "
            "about; do not invent categories, features, or distinctions (e.g. "
            "'plans', 'subscriptions') that aren't reflected in that list."
        )
    else:
        topics = "No close match was found in the Site24x7 KB for this ticket."
        grounding = (
            "No close match was found in the Site24x7 KB for this ticket. Ask "
            "exactly ONE short question that would help identify what the customer "
            "actually needs; do not guess at a specific feature or invent a "
            "distinction that may not apply."
        )
    text = provider.complete_text(
        "A customer support ticket for Site24x7 (an infrastructure monitoring "
        f"product) is too ambiguous to classify confidently. {grounding} Do not "
        "answer the ticket, do not ask a generic 'can you tell me more?', and do "
        "not assume details the customer hasn't given you."
        f"{_EMAIL_BODY_NOTE if data.reply_format == 'email' else ' ' + _CHAT_STYLE}",
        {
            "best_guess": f"{data.category or 'unknown'} (confidence {data.confidence:.2f})",
            "candidate_topics": topics,
            "ticket": data.ticket_text,
        },
    )
    return _frame(text, data.reply_format, data.customer_name)


def is_continuation(data: ContinuationInput) -> ContinuationVerdict:
    return provider.complete_json(
        "Decide whether the customer's new message is a CONTINUATION of this "
        "support conversation (it refers back to, asks about, elaborates on, or "
        "compares something in the agent's last reply) or a NEW, unrelated "
        "request that has nothing to do with that reply.",
        {"agent_last_reply": data.prior_ai_response, "customer_new_message": data.message},
        ContinuationVerdict,
    )
