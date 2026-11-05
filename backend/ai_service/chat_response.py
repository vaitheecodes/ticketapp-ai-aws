"""chat_response: what the customer reads.

- answer: a KB-grounded reply to the request (chat or email format).
- info_request: for an "escalate_with_info" KB entry: numbered steps to
  collect what support needs, the collect list and a fixed safety note,
  never a question (the caller raises the ticket in the same turn).
- clarify: one targeted question naming the real candidate topics.
- is_continuation: does a follow-up refer back to the AI's last answer?

Every reply is filtered by code: a request for the exact error message is
dropped unless the customer reported an error; a how-to answer loses the
"if you encounter issues..." boilerplate; numbered or bulleted steps the
top KB entry doesn't support are dropped (ai_service/grounding.py), so an
answer's steps always come from the knowledge base.

Email-format replies (ticket-type requests) are body-only from the model
and wrapped here with the fixed greeting/closing/signature, so every
email has the same frame. Chat replies are a live conversation: no
greeting, sign-off or signature, and clean_chat_reply removes any the
model writes anyway.
"""

from __future__ import annotations

import re
from contextlib import nullcontext

from backend import email_utils
from backend.ai_service import backends, grounding, provider
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
    "Name], and never sign with a name or team. Ask at most one question per "
    "reply."
)

# What to ask for. The AI has no account access, so an identifier helps
# nobody; these details are what the customer can give and the AI can use.
_ASKING_RULES = (
    "When you do need more information: you cannot look up the customer's "
    "account, monitors or settings, so never ask for a monitor name, monitor ID, "
    "account ID, email address, API key or any other identifier (only someone "
    "with account access could use it, even if the kb_guidance block lists it as "
    "a detail to provide). Ask instead for something the customer can tell you "
    "and you can use: the monitor type (website, server, API, ping, port, DNS, "
    "SSL, cloud, network device, ...), {detail}, the screen or step "
    "where it happens, or what they have already tried. Ask which type of "
    "monitor it is, never which monitor, and never ask the customer to confirm "
    "something they have already told you."
)
_ERROR_DETAIL = "the exact error text"
_NO_ERROR_DETAIL = ("what they see on the screen (the customer hasn't reported an error, so "
                    "never ask for an error message)")
# The answer op only: the asking rules above made Nova Lite ask before
# answering requests the KB guidance fully covered.
_ANSWER_FIRST = (
    "If the kb_guidance block covers the request, answer it now: don't ask a "
    "question first. "
)
# Added only when the monitor type is unknown and there is nothing to
# answer from yet (always for a clarifying question): with KB guidance
# that covers the request, the model should answer, not ask.
_MONITOR_TYPE_FIRST = (
    " The customer hasn't said what type of monitor it is: ask for the monitor "
    "type first."
)
_ASSUMPTION_RULES = (
    "Never assume a product, integration, monitor type or feature the customer "
    "has not mentioned. The unconfirmed_topics block lists knowledge-base topics "
    "that matched some of the customer's words but are specific to something "
    "they have not mentioned: never answer as if one of them applies. Ask "
    "whether it applies, or first ask for the missing detail. This is no reason "
    "to hold back an answer the kb_guidance block does give: when it covers what "
    "the customer asked, answer."
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
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_LIST_MARKER_ONLY = re.compile(r"^\s*([-*•]|\d+[.)])?\s*$")
# A request for an identifier: "Can you share the monitor name?",
# "Please confirm the API key ...". Advice that only mentions one ("check
# the API key in the integration settings") has no asking verb and stays.
_IDENTIFIER = re.compile(
    r"\b(monitor|account|customer|device|resource|licen[cs]e|user|integration|api|access)"
    r"[ -]?(name|id|identifier|number|key|email)s?\b|\bname of (the|your) (monitor|device|account)\b"
    r"|\bemail address\b"
    r"|\bwhich (specific |particular )?(service|monitor|server|device|resource|integration)s?\b",
    re.IGNORECASE)
_ASKING = re.compile(r"\b(provide|share|send|give|tell|confirm|let (me|us) know)\b|^\s*(what|which)\b",
                     re.IGNORECASE)
MONITOR_TYPE_QUESTION = ("What type of monitor is it: website, server, API, ping, port, DNS, "
                         "cloud or network device?")
# Only when the customer reported an error (request_kind.error_reported).
DETAIL_QUESTION = "What is the exact error message, and at which screen or step does it appear?"
STEP_QUESTION = "At which screen or step are you, and what do you see there?"
# A sentence asking about an error message the customer never mentioned.
_ERROR_REQUEST = re.compile(r"\berror (message|text|code|details?)s?\b|\bexact error\b|\berror (you|that) (see|get)",
                            re.IGNORECASE)
# How-to closing boilerplate: "If you encounter issues, ...", "Let me know if ...".
_BOILERPLATE = re.compile(
    r"^\s*(if you (encounter|run into|face|have|experience|hit|get|see|need)\b|feel free\b|let me know if\b"
    r"|(please )?do(n't| not) hesitate\b|hope this helps\b)", re.IGNORECASE)


def _drop_identifier_requests(lines: list[str]) -> tuple[list[str], bool]:
    """Drop sentences asking for an identifier; True if any were dropped."""
    out, dropped = [], False
    for line in lines:
        kept = []
        for sentence in _SENTENCE_END.split(line):
            if _IDENTIFIER.search(sentence) and (sentence.rstrip().endswith("?") or _ASKING.search(sentence)):
                dropped = True
                continue
            kept.append(sentence)
        new = " ".join(kept)
        if new != line and _LIST_MARKER_ONLY.match(new):
            continue
        out.append(new)
    return out, dropped


def _drop_sentences(lines: list[str], unwanted) -> list[str]:
    """Drop the sentences `unwanted(sentence)` is true for; a line left
    with nothing but a list marker goes too."""
    out = []
    for line in lines:
        new = " ".join(s for s in _SENTENCE_END.split(line) if not unwanted(s))
        if new != line and (not new.strip() or _LIST_MARKER_ONLY.match(new)):
            continue
        out.append(new)
    return out


def drop_unasked(text: str, error_reported: bool = True, how_to: bool = False) -> str:
    """Drop requests for an error message the customer never mentioned,
    and, for a how-to, the "if you encounter issues..." boilerplate."""
    lines = text.splitlines()
    if not error_reported:
        lines = _drop_sentences(lines, lambda s: bool(_ERROR_REQUEST.search(s)))
    if how_to:
        lines = _drop_sentences(lines, lambda s: bool(_BOILERPLATE.match(s)))
    return "\n".join(lines)


def _first_question_only(lines: list[str]) -> list[str]:
    """Drop every question sentence after the first; a line left with
    nothing but a list marker goes too."""
    out, asked = [], False
    for line in lines:
        kept = []
        for sentence in _SENTENCE_END.split(line):
            if sentence.rstrip().endswith("?"):
                if asked:
                    continue
                asked = True
            kept.append(sentence)
        new = " ".join(kept)
        if new != line and _LIST_MARKER_ONLY.match(new):
            continue
        out.append(new)
    return out


def detail_question(monitor_type_known: bool, error_reported: bool) -> str:
    """The follow-up question to ask: the monitor type when it isn't
    known, the exact error only when the customer reported one, else
    where they are and what they see."""
    if not monitor_type_known:
        return MONITOR_TYPE_QUESTION
    return DETAIL_QUESTION if error_reported else STEP_QUESTION


def clean_chat_reply(text: str, monitor_type_known: bool = True, error_reported: bool = True,
                     how_to: bool = False) -> str:
    """Strip greeting lines, the sign-off and signature, [Placeholder]
    text, requests for identifiers, error-message requests the customer
    gave no reason for (drop_unasked), and every question after the
    first from a chat reply. A dropped identifier request is replaced by
    a useful question (detail_question) unless the reply still asks one.
    A reply with nothing left (only placeholders and pleasantries) is an
    AI failure."""
    text = drop_unasked(text, error_reported, how_to)
    lines, dropped = _drop_identifier_requests(_PLACEHOLDER.sub("", text).splitlines())
    if dropped and not any("?" in line for line in lines):
        lines.append(detail_question(monitor_type_known, error_reported))
    lines = _first_question_only(lines)
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
        raise provider.AIError("AI chat reply was only placeholder text",
                               provider.AIFailure(kind="filter_rejected", operation="chat_filter",
                                                  code="only_placeholders"))
    return cleaned


def _ground(text: str, data: AnswerInput, operation: str) -> str:
    """An answer's steps must come from the KB entry it was given (the
    top candidate, the one in the kb_guidance block): unsupported steps
    are dropped and the drop is recorded for the admin."""
    grounded, dropped = grounding.ground_steps(text, grounding.kb_text(data.kb_candidates[:1]))
    if dropped:
        provider.note_failure(provider.AIFailure(
            kind="filter_rejected", operation=operation, code="ungrounded_steps", outcome="filtered"))
    if not re.search(r"[A-Za-z0-9]", grounded):
        raise provider.AIError("AI reply was only ungrounded steps",
                               provider.AIFailure(kind="filter_rejected", operation=operation,
                                                  code="only_ungrounded_steps"))
    return grounded


def _frame(text: str, data: AnswerInput | ClarifyInput, operation: str = "") -> ChatReply:
    if isinstance(data, AnswerInput):
        text = _ground(text, data, operation or "answer")
    if data.reply_format == "email":
        text = email_utils.format_email_reply(
            data.customer_name, drop_unasked(text, data.error_reported, data.how_to))
    else:
        text = clean_chat_reply(text, monitor_type_known=data.monitor_type_known,
                                error_reported=data.error_reported, how_to=data.how_to)
    return ChatReply(text=text)


def _asking_rules(data: AnswerInput | ClarifyInput, answering: bool) -> str:
    ask_type_first = not data.monitor_type_known and not (answering and data.kb_candidates)
    rules = _ASKING_RULES.format(detail=_ERROR_DETAIL if data.error_reported else _NO_ERROR_DETAIL)
    return ((_ANSWER_FIRST if answering and data.kb_candidates else "") + rules
            + (_MONITOR_TYPE_FIRST if ask_type_first else ""))


def _context_blocks(data: AnswerInput | ClarifyInput) -> dict[str, str]:
    return {
        "customer_details": data.customer_details or "(none)",
        "unconfirmed_topics": "\n".join(f"- {t}" for t in data.unconfirmed_topics) or "(none)",
    }


# ---------------------------------------------------------------------------
# One reply: regenerate or fall back instead of failing on our own checks
# ---------------------------------------------------------------------------
REPEAT_OVERLAP = 0.8
TRIED_QUESTION = "What have you already tried so far?"
OFFER_TICKET_CHAT = ("I can't narrow this down any further here. Use Raise a ticket below and our "
                     "support team will pick it up with this whole conversation.")
OFFER_TICKET_EMAIL = "Our support team will look into this and reply here."
_REGENERATE_NOTE = (
    "\n\nYour previous reply was empty once greetings, sign-offs and placeholder "
    "text were removed. Write the actual reply: an answer, or one useful question."
)
_MOVE_ON_NOTE = (
    "\n\nYour reply repeated one of your previous replies (the "
    "your_previous_replies block). Don't repeat them: move the conversation "
    "forward. Answer the customer's latest message using what they have said "
    "since, or ask a different question."
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def repeats(text: str, previous: list[str]) -> bool:
    """Identical or near-identical (>= 80% word overlap) to any previous reply."""
    words = _words(text)
    for prior in previous:
        other = _words(prior)
        if words and other and len(words & other) / max(len(words), len(other)) >= REPEAT_OVERLAP:
            return True
    return False


def open_question(monitor_type: str | None) -> str:
    """For a customer who hasn't described a problem yet ("I have an issue")."""
    if monitor_type:
        subject = f"your {monitor_type}" if monitor_type.endswith("monitoring") else f"your {monitor_type} monitor"
    else:
        subject = "your monitoring"
    return (f"What are you seeing, or trying to do, with {subject}? For example: no data, a wrong "
            "status, alerts, the agent, or setup.")


def _fallback_reply(data: AnswerInput | ClarifyInput) -> ChatReply:
    """A useful question that doesn't repeat a recent reply, else an offer
    of a ticket (chat) or a note that the team will look into it (email)."""
    options = ([open_question(data.monitor_type)] if data.vague else []) + (
        [MONITOR_TYPE_QUESTION] if not data.monitor_type_known else []) + [
        DETAIL_QUESTION if data.error_reported else STEP_QUESTION, TRIED_QUESTION]
    for question in options:
        reply = ChatReply(text=email_utils.format_email_reply(data.customer_name, question)
                          if data.reply_format == "email" else clean_chat_reply(question))
        if not repeats(reply.text, data.recent_ai_replies):
            return reply
    if data.reply_format == "email":
        return ChatReply(text=email_utils.format_email_reply(data.customer_name, OFFER_TICKET_EMAIL))
    return ChatReply(text=clean_chat_reply(OFFER_TICKET_CHAT))


def _reply(instructions: str, blocks: dict[str, str], data: AnswerInput | ClarifyInput,
           operation: str) -> ChatReply:
    """Generate, clean and check one reply. A reply rejected by our own
    checks (empty, only placeholders, a repeat of one of the last replies)
    is regenerated once with a note, then replaced by a useful fallback
    question: never an "AI unavailable" error. Provider errors (throttling,
    timeouts, access...) still raise AIError."""
    note, extra = "", {}
    for attempt in (1, 2):
        try:
            reply = _frame(provider.complete_text(instructions + note, {**blocks, **extra},
                                                  operation=operation), data, operation)
        except provider.AIError as exc:
            if exc.failure is None or exc.failure.kind not in ("parse_schema", "filter_rejected"):
                raise
            exc.failure.outcome = "regenerated" if attempt == 1 else "fallback"
            if exc.failure.kind == "parse_schema":  # complete_text already retried
                exc.failure.outcome = "fallback"
                break
            note = _REGENERATE_NOTE
            continue
        if not repeats(reply.text, data.recent_ai_replies):
            return reply
        provider.note_failure(provider.AIFailure(
            kind="filter_rejected", operation=operation, code="repeated_reply", attempts=attempt,
            outcome="regenerated" if attempt == 1 else "fallback"))
        note = _MOVE_ON_NOTE
        extra = {"your_previous_replies": "\n---\n".join(data.recent_ai_replies)}
    return _fallback_reply(data)


def answer(data: AnswerInput) -> ChatReply:
    if data.vague:
        return _vague_reply(data)
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
    how_to_note = (
        "\n\nThis is a how-to request (the customer wants to add, set up or configure "
        "something), not a fault: nothing is broken. Give the steps the kb_guidance block "
        "gives and nothing else: no troubleshooting, no request for an error message, and "
        "no closing line such as 'if you encounter issues...'."
        + (" The kb_guidance guide was matched to what the customer asked for: answer with "
           "its steps now, without first asking which type of monitor or anything else."
           if data.kb_candidates else "")
        if data.how_to else "")
    no_match_note = ""
    if not data.kb_candidates:
        offer = ("offer to raise a ticket so the support team can look into it"
                 if data.reply_format == "chat" else "say the support team will look into it")
        no_match_note = (
            "\n\nThe kb_guidance block has no close knowledge-base match for this "
            "request. Do not answer from general knowledge and do not give "
            "troubleshooting steps. Either ask exactly one short question that would "
            f"help identify the problem, or {offer}."
        )
    # The customer-facing chat answer may use its own model
    # (BEDROCK_MODEL_ID_CHAT); every other call keeps BEDROCK_MODEL_ID.
    with backends.model_task("chat") if data.reply_format == "chat" else nullcontext():
        return _answer_reply(data, role, no_match_note + how_to_note)


def _answer_reply(data: AnswerInput, role: str, no_match_note: str) -> ChatReply:
    return _reply(
        f"{role}{no_match_note}\n\n"
        "When the customer's latest message (the customer_latest_message block) "
        "contains a clear question, answer that question first, even if earlier "
        "messages were about something else.\n\n"
        "Base the answer on the kb_guidance block, personalised to the customer's "
        "own wording; don't contradict it. Use the order_record block as the only "
        "source of truth for order facts. If answering would need a specific fact "
        "you weren't given (an order status, account detail, setting value), say "
        "plainly that you don't have access to it and ask for it, or say a person "
        f"will need to check — never guess. {GROUNDING_RULES} Every step you give must "
        "come from the kb_guidance block: never add a menu path, setting, hostname, port "
        "or number it doesn't contain; steps it doesn't support are removed.\n\n"
        f"{_asking_rules(data, answering=True)}\n\n{_ASSUMPTION_RULES}",
        {
            "category": data.category or "unknown",
            "customer_history": history_text(data.history),
            **_context_blocks(data),
            "kb_guidance": kb_guidance_text(data.kb_candidates),
            "order_record": data.order_context or "(none)",
            "customer_latest_message": data.latest_message or "(see the ticket block)",
            "ticket": data.ticket_text,
        },
        data,
        "answer",
    )


def _vague_reply(data: AnswerInput | ClarifyInput) -> ChatReply:
    """No symptom described yet: one open question, no KB grounding and
    no model call, so no scenario can be assumed."""
    reply = _frame(open_question(data.monitor_type), data)
    return _fallback_reply(data) if repeats(reply.text, data.recent_ai_replies) else reply


_HIGH_RISK_CLARIFY = (
    "A Site24x7 (infrastructure monitoring product) customer reported a problem about "
    "their account, licensing, billing or data, and it isn't clear yet what they mean. "
    "A support ticket will be raised after their answer. Ask exactly ONE short question "
    "that pins down what is still unknown, using their own words: for a sign-in problem, "
    "which login (the web console or the mobile app) and what appears on the screen; for "
    "a vague phrase such as 'changing license', what exactly they changed. Do not suggest "
    "causes or solutions, do not mention knowledge-base topics, and do not answer the "
    "request.")


def clarify(data: ClarifyInput) -> ChatReply:
    if data.vague:
        return _vague_reply(data)
    if data.high_risk:
        return _reply(
            f"{_HIGH_RISK_CLARIFY}"
            f"{_EMAIL_BODY_NOTE if data.reply_format == 'email' else ' ' + _CHAT_STYLE}\n\n"
            f"{_asking_rules(data, answering=False)}",
            {"customer_details": data.customer_details or "(none)", "ticket": data.ticket_text},
            data,
            "clarify",
        )
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
    return _reply(
        "A customer support ticket for Site24x7 (an infrastructure monitoring "
        f"product) is too ambiguous to classify confidently. {grounding} Do not "
        "answer the ticket, do not ask a generic 'can you tell me more?', and do "
        "not assume details the customer hasn't given you."
        f"{_EMAIL_BODY_NOTE if data.reply_format == 'email' else ' ' + _CHAT_STYLE}\n\n"
        f"{_asking_rules(data, answering=False)}\n\n{_ASSUMPTION_RULES}",
        {
            "best_guess": f"{data.category or 'unknown'} (confidence {data.confidence:.2f})",
            **_context_blocks(data),
            "candidate_topics": topics,
            "ticket": data.ticket_text,
        },
        data,
        "clarify",
    )


# ---------------------------------------------------------------------------
# info_request: "escalate_with_info" KB entries
# ---------------------------------------------------------------------------
INFO_SAFETY_NOTE = ("Files such as a HAR file or logs can contain cookies, tokens or passwords, so "
                    "attach them only to your support ticket, never in this chat or by email.")
INFO_COLLECT_INTRO = "Please add these to your ticket:"
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S")
# A sentence about sensitive data: INFO_SAFETY_NOTE replaces it.
_SENSITIVE = re.compile(r"\b(cookies?|tokens?|passwords?)\b", re.IGNORECASE)


def numbered_steps(text: str) -> str | None:
    """`text` if it gives at least two numbered steps and asks nothing,
    with any sentence about sensitive data removed; else None."""
    if "?" in text:
        return None
    lines = []
    for line in text.splitlines():
        kept = " ".join(s for s in _SENTENCE_END.split(line) if not _SENSITIVE.search(s))
        if line.strip() and (not kept.strip() or _LIST_MARKER_ONLY.match(kept)):
            continue
        lines.append(kept.rstrip())
    if sum(1 for line in lines if _NUMBERED.match(line)) < 2:
        return None
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def steps_from_answer(answer_text: str) -> str:
    """The approved answer itself as numbered steps (one per line), used
    when the model's rewrite fails the checks."""
    lines = [line.strip() for line in answer_text.splitlines() if line.strip()]
    if len(lines) == 1:
        lines = [s for s in _SENTENCE_END.split(lines[0]) if s.strip()]
    lines = [line for line in lines if not _SENSITIVE.search(line)] or lines
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))


def info_request(data: AnswerInput) -> ChatReply:
    """The reply for an "escalate_with_info" entry (data.kb_candidates[0]):
    numbered steps from its approved answer (rewritten by the model, or the
    answer's own lines if the rewrite has a question or no numbered steps),
    then what to add to the ticket and INFO_SAFETY_NOTE, both added by code."""
    entry = data.kb_candidates[0]
    steps = None
    try:
        with backends.model_task("chat") if data.reply_format == "chat" else nullcontext():
            raw = provider.complete_text(
                "You are the Site24x7 support assistant (Site24x7 is an infrastructure "
                "monitoring product). Our support team needs some details from the "
                "customer to investigate, and a ticket is being raised for them now. "
                "Rewrite the guidance in the kb_guidance block as short numbered steps "
                "the customer follows (\"1.\", \"2.\", ...), one line each, keeping "
                "browser names, menu names and button labels exactly as written there. "
                "Where the guidance gives alternatives (for example one line per "
                "browser), keep each alternative as ONE step starting with its name "
                "(\"Chrome or Edge: ...\"); never split it into several steps. "
                "You may start with one short sentence saying what is needed. Use only "
                "what the kb_guidance block says. Do not ask any question. Do not mention "
                "cookies, tokens, passwords or the ticket: those notes are added for you. "
                "No greeting, no sign-off, no signature, no placeholder text.",
                {"kb_guidance": kb_guidance_text([entry]),
                 "customer_latest_message": data.latest_message or "(see the ticket block)",
                 "ticket": data.ticket_text},
                operation="info_request",
            )
        cleaned = raw if data.reply_format == "email" else clean_chat_reply(raw)
        steps = numbered_steps(cleaned)
        if steps is None:
            provider.note_failure(provider.AIFailure(
                kind="filter_rejected", operation="info_request", code="not_numbered_steps",
                outcome="fallback"))
    except provider.AIError as exc:
        if exc.failure is None or exc.failure.kind not in ("parse_schema", "filter_rejected"):
            raise
        exc.failure.outcome = "fallback"
    body = steps or steps_from_answer(entry.response)
    if entry.collect:
        body += "\n\n" + INFO_COLLECT_INTRO + "\n" + "\n".join(f"- {item}" for item in entry.collect)
    body += "\n\n" + INFO_SAFETY_NOTE
    if data.reply_format == "email":
        return ChatReply(text=email_utils.format_email_reply(data.customer_name, body))
    return ChatReply(text=body)


def is_continuation(data: ContinuationInput) -> ContinuationVerdict:
    return provider.complete_json(
        "Decide whether the customer's new message is a CONTINUATION of this "
        "support conversation (it refers back to, asks about, elaborates on, or "
        "compares something in the agent's last reply) or a NEW, unrelated "
        "request that has nothing to do with that reply.",
        {"agent_last_reply": data.prior_ai_response, "customer_new_message": data.message},
        ContinuationVerdict,
    )
