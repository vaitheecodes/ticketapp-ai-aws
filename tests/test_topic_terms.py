"""No unstated assumptions: KB entries specific to a product, integration
or monitor type only ground an answer when the customer has mentioned it.

- topic_terms: what a customer mentioned, what a KB entry depends on
  (its intent, plus integration/cloud names in its answer; monitor names
  like "Azure-VM-WebApp-01" don't count).
- graph.grounding_candidates: the real test chat ("... invalid data
  provided for api error") retrieved a PagerDuty entry; it is kept out of
  the grounding and passed as an unconfirmed topic instead.
- A customer-picked category narrows grounding but never classification
  or routing (the high-risk bar can't be picked around).
"""

from __future__ import annotations

import pytest

from backend import graph, kb_retrieval, topic_terms
from backend.ai_service import chat_response
from backend.ai_service.schemas import Categorization

CHAT = [
    "I am facing issue in adding monitor",
    "AIOPS-Entra-Moniitor, I am getting invalid data provided for api error",
    "that is not the issue",
]


def _customer_text(messages):
    return "\n".join(messages)


def _base_candidates(text):
    """Retrieval over the exported KB only (kb_entries.json), as in the
    test chat: approved hand-written entries change what ranks first."""
    import json

    entries = json.loads(kb_retrieval.KB_PATH.read_text(encoding="utf-8"))["entries"]
    hits = kb_retrieval.build_index(entries).search(text, top_k=3, min_score=kb_retrieval.kb_min_score())
    return graph._kb_candidates_to_dicts(hits)


def _transcript(messages):
    return "\n".join(f"Customer: {m}\nAgent (AI): (reply)" for m in messages)


@pytest.fixture(autouse=True)
def _require_kb():
    if kb_retrieval._load_index() is None:
        pytest.skip("data/kb/kb_entries.json not found")


# ---------------------------------------------------------------------------
# topic_terms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("I am facing issue in adding monitor", set()),
    # "for api": the Site24x7 error names the API monitor.
    ("AIOPS-Entra-Moniitor, I am getting invalid data provided for api error", {"API"}),
    ("AIOPS-Entra-Moniitor, I am getting invalid data provided error", set()),
    ("I lost my API key", set()),
    ("my website shows down", {"website"}),
    ("the REST API monitor fails with 401", {"API"}),
    ("PagerDuty alerts are not created", {"PagerDuty"}),
    ("our Linux server stopped reporting", {"server", "Linux"}),
    ("SNMP traps from the router", {"network device (SNMP)"}),
    ("ssl certificate expiry alert", {"SSL certificate"}),
])
def test_what_the_customer_mentioned(text, expected):
    assert topic_terms.mentioned(text) == expected


def test_monitor_type_from_words_or_picked_category():
    assert topic_terms.monitor_types("I am facing issue in adding monitor") == []
    assert topic_terms.monitor_types("my website is slow") == ["website"]
    assert topic_terms.monitor_types("adding a monitor fails", "SERVER_MONITORING") == ["server"]
    assert topic_terms.monitor_types("adding a monitor fails", "ALERTS") == []
    # The customer's own words win over the picked category.
    assert topic_terms.monitor_types("ping check fails", "SERVER_MONITORING") == ["ping"]


def test_only_specific_intents_have_terms_in_the_real_kb():
    index = kb_retrieval._load_index()
    by_intent = {}
    for e in index.entries:
        by_intent.setdefault(e.intent, set()).update(topic_terms.entry_terms(e.intent, e.response))
    assert by_intent["pagerduty_integration"] == {"PagerDuty"}
    assert by_intent["api_monitoring"] == {"API"}
    for generic in ("contact_support", "monitor_down", "monitor_not_discovered", "alert_configuration",
                    "integrations", "licensing"):
        assert by_intent[generic] == set(), generic
    # Registered with the approved Linux / Windows agent guides.
    assert by_intent["agent_installation"] == {"Linux", "Windows"}
    # Nothing beyond the intent map (built-in plus data/kb/kb_registry.json):
    # monitor names in answers don't count.
    assert all(terms == (topic_terms.intent_terms(intent) or set()) for intent, terms in by_intent.items())


def test_answer_names_make_a_new_entry_specific():
    assert topic_terms.entry_terms("custom_intent", "Open the Slack integration and re-authorise.") == {"Slack"}
    assert topic_terms.entry_terms("monitor_down", "Azure-VM-WebApp-01 is showing down.") == set()


def test_missing_terms():
    pagerduty = {"intent": "pagerduty_integration", "response": "Check the PagerDuty key."}
    assert topic_terms.missing_terms(pagerduty, CHAT[1]) == {"PagerDuty"}
    assert topic_terms.missing_terms(pagerduty, "pagerduty says rate limit") == set()
    assert topic_terms.missing_terms({"intent": "contact_support", "response": "Share details."}, CHAT[1]) == set()


# ---------------------------------------------------------------------------
# Grounding in the graph
# ---------------------------------------------------------------------------
def test_the_test_chat_pagerduty_match_is_not_grounding():
    state = {"ticket_text": _transcript(CHAT[:2])}
    state["kb_candidates"] = _base_candidates(_customer_text(CHAT[:2]))
    assert "pagerduty_integration" in [c["intent"] for c in state["kb_candidates"]]  # what retrieval found
    usable, unconfirmed = graph.grounding_candidates(state)
    assert all(c["intent"] != "pagerduty_integration" for c in usable)
    assert ("pagerduty_integration", {"PagerDuty"}) in [(c["intent"], terms) for c, terms in unconfirmed]


def test_a_specific_entry_grounds_once_the_customer_names_it():
    state = {"ticket_text": "Customer: I am getting a PagerDuty rate limit error"}
    state.update(graph.retrieve_kb_candidates(state))
    usable, unconfirmed = graph.grounding_candidates(state)
    assert usable[0]["intent"] == "pagerduty_integration" and unconfirmed == []


def test_reply_context_tells_the_prompt_what_is_known(monkeypatch):
    seen = {}

    def fake_answer(data):
        seen["data"] = data
        return chat_response.ChatReply(text="What type of monitor is it?")

    monkeypatch.setattr(chat_response, "answer", fake_answer)
    state = {"ticket_text": _transcript(CHAT[:2]), "reply_format": "chat"}
    state["kb_candidates"] = _base_candidates(_customer_text(CHAT[:2]))
    graph.generate_response(state)
    data = seen["data"]
    assert all(c.intent != "pagerduty_integration" for c in data.kb_candidates)
    assert any("PagerDuty" in topic for topic in data.unconfirmed_topics)
    assert "Monitor type: API (stated by the customer)" in data.customer_details
    assert data.monitor_type_known is True


def test_picked_category_narrows_grounding(monkeypatch):
    seen = {}
    monkeypatch.setattr(chat_response, "answer",
                        lambda data: seen.setdefault("data", data) and chat_response.ChatReply(text="ok"))
    text = "Customer: my monitor is showing down"
    state = {"ticket_text": text, "reply_format": "chat", "requested_category": "WEB_MONITORING"}
    state.update(graph.retrieve_kb_candidates(state))
    assert any(c["category"] != "WEB_MONITORING" for c in state["kb_candidates"])
    graph.generate_response(state)
    data = seen["data"]
    assert data.kb_candidates and {c.category for c in data.kb_candidates} == {"WEB_MONITORING"}
    assert "Category the customer picked: WEB_MONITORING" in data.customer_details
    assert "Monitor type: website or API" in data.customer_details


def test_picked_category_with_no_close_match_keeps_the_normal_search():
    state = {"ticket_text": "Customer: how do I install the Site24x7 agent on Linux", "requested_category": "NETFLOW"}
    state.update(graph.retrieve_kb_candidates(state))
    usable, _ = graph.grounding_candidates(state)
    assert usable[0]["intent"] == "agent_installation"


@pytest.mark.parametrize("picked", [None, "MONITORING", "WEB_MONITORING"])
def test_picked_category_never_changes_classification(monkeypatch, picked):
    """An unsure ACCOUNT (high-risk) classification stays ACCOUNT whatever
    the customer picked: the pick never reaches classify_ticket."""
    monkeypatch.setattr(graph.categorize_op, "categorize_ticket",
                        lambda data: Categorization(category="ACCOUNT", confidence=0.5))
    state = {"ticket_text": "Customer: how do I upgrade my license plan", "requested_category": picked}
    state.update(graph.retrieve_kb_candidates(state))
    assert graph.classify_ticket(state)["category"] == "ACCOUNT"


def test_an_unconfirmed_strong_match_does_not_override_classification(monkeypatch):
    """The API-specific HAR entry (KB-N0140) scores above STRONG_MATCH_SCORE
    for the test chat without "for api"; unconfirmed, it doesn't move an
    unsure classification to WEB_MONITORING."""
    monkeypatch.setattr(graph.categorize_op, "categorize_ticket",
                        lambda data: Categorization(category="MONITORING", confidence=0.5))
    state = {"ticket_text": _transcript([CHAT[0], CHAT[1].replace(" for api", "")])}
    state.update(graph.retrieve_kb_candidates(state))
    assert state["kb_candidates"][0]["score"] >= kb_retrieval.STRONG_MATCH_SCORE
    assert graph.classify_ticket(state)["category"] == "MONITORING"


def test_a_guide_for_another_variant_of_its_intent():
    linux = {"intent": "agent_installation", "instruction": "How do I install the agent on a Linux server?",
             "response": "1. Download the Linux installer."}
    generic = {"intent": "agent_installation", "instruction": "How do I install the agent?", "response": "Install it."}
    assert topic_terms.names_another_variant(linux, "install the agent on Windows")
    assert not topic_terms.names_another_variant(linux, "install the agent on Linux")
    assert not topic_terms.names_another_variant(linux, "install the agent")  # nothing named: missing_terms asks
    assert not topic_terms.names_another_variant(generic, "install the agent on Windows")
