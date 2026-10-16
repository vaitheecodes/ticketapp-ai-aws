"""Stage 2 customer ticket model: ticket_type (chat | ticket), the five
statuses, email-format AI replies, the My tickets list, the thread
detail, reply/reopen, and the attachment endpoint.

Real temporary SQLite DB via FastAPI's TestClient; the pipeline
(backend.runtime) is replaced by a stub, and the ticket hand-off summary by a canned one, so no
LLM call is made. The email-format wrapping in graph.generate_response is
tested separately against a stubbed Azure OpenAI client.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, email_utils, graph, main, runtime  # noqa: E402
from backend.ai_service import provider, summarize_ticket  # noqa: E402
from backend.ai_service.schemas import HandoffSummary  # noqa: E402

_counter = itertools.count()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4\n%fake\n"
TXT = b"2026-10-16 12:00:01 ERROR agent heartbeat missed\n"


class _StubGraph:
    def __init__(self, results):
        self._results = list(results)
        self.invocations = []

    def invoke(self, state):
        self.invocations.append(state)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _ai(answer="Hi Dana,\n\nInstall the agent...\n\nRegards,\nSite24x7 Support Team"):
    return {"route": "ai", "category": "AGENT", "confidence": 0.95,
            "ai_response": answer, "clarification_attempts": 0}


_HUMAN = {"route": "human", "category": "ACCOUNT", "confidence": 0.4, "clarification_attempts": 0}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(summarize_ticket, "summarize_ticket",
                        lambda thread, reason: HandoffSummary(issue="CASE SUMMARY"))
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def sent_emails(monkeypatch):
    sent = []
    monkeypatch.setattr(email_utils, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def _customer(client, name="Dana Smith"):
    email = f"cust-{next(_counter)}@example.com"
    res = client.post("/api/auth/customer/signup",
                      json={"name": name, "email": email, "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}, email


def _raise(client, headers, result, **overrides):
    runtime.set_graph(_StubGraph([result]))
    body = {"subject": "Agent not reporting", "description": "Installed the Linux agent, no data.",
            "priority": "high", "category": "AGENT"}
    body.update(overrides)
    res = client.post("/api/tickets", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _start_chat(client, headers, result, text="how do I install the agent"):
    runtime.set_graph(_StubGraph([result]))
    res = client.post("/api/chats", json={"issue": text, "category": "AGENT"}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _row(ticket_id):
    conn = db.get_connection()
    try:
        return dict(conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone())
    finally:
        conn.close()


def _upload(client, headers, ticket_id, message_id, files):
    return client.post(
        f"/api/tickets/{ticket_id}/attachments",
        data={"message_id": message_id},
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=headers,
    )


# ---------------------------------------------------------------------------
# ticket_type
# ---------------------------------------------------------------------------
def test_raise_ticket_creates_ticket_type_with_form_fields(client):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    assert created["ticket_type"] == "ticket"
    assert created["status"] == "resolved"

    row = _row(created["ticket_id"])
    assert row["ticket_type"] == "ticket"
    assert row["subject"] == "Agent not reporting"
    assert row["priority"] == "high"
    assert row["requested_category"] == "AGENT"
    # Technicians only see ticket_text, so it carries subject + description.
    assert row["ticket_text"] == "Agent not reporting\n\nInstalled the Linux agent, no data."


def test_raise_ticket_asks_pipeline_for_an_email_reply(client):
    headers, _ = _customer(client, name="Dana Smith")
    _raise(client, headers, _ai())
    state = runtime.get_graph().invocations[0]
    assert state["reply_format"] == "email"
    assert state["customer_name"] == "Dana Smith"


def test_chats_only_become_tickets_when_escalated(client):
    headers, _ = _customer(client)
    active = _start_chat(client, headers, _ai("Here's how..."))
    assert active["ticket_id"] is None
    escalated = _start_chat(client, headers, _HUMAN, text="cancel my licence")
    row = _row(escalated["ticket_id"])
    assert row["ticket_type"] == "chat"
    assert row["chat_session_id"] == escalated["session_id"]
    assert runtime.get_graph().invocations[0]["reply_format"] == "chat"


def test_invalid_category_and_priority_are_rejected(client):
    headers, _ = _customer(client)
    runtime.set_graph(_StubGraph([_ai()]))
    bad_cat = client.post("/api/tickets", json={"subject": "s", "description": "d", "category": "NOPE"},
                          headers=headers)
    bad_pri = client.post("/api/tickets", json={"subject": "s", "description": "d", "priority": "asap"},
                          headers=headers)
    assert bad_cat.status_code == 422
    assert bad_pri.status_code == 422


# ---------------------------------------------------------------------------
# Statuses: open / in_progress / pending_customer / resolved / closed
# ---------------------------------------------------------------------------
def test_status_for_each_route(client):
    headers, _ = _customer(client)
    clarify = {"route": "clarify", "category": "ALERTS", "confidence": 0.5,
               "ai_response": "Which alert?", "clarification_attempts": 1}
    assert _raise(client, headers, _ai())["status"] == "resolved"
    assert _raise(client, headers, clarify)["status"] == "pending_customer"
    assert _raise(client, headers, _HUMAN)["status"] == "open"


def test_technician_pickup_moves_ticket_to_in_progress(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _HUMAN)["ticket_id"]
    login = client.post("/api/auth/login",
                        json={"email": "technician@example.com", "password": "technician-demo-pass"})
    tech = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech).json()["ticket"]["status"] == "in_progress"
    assert _row(ticket_id)["status"] == "in_progress"


def test_legacy_statuses_are_migrated(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _HUMAN)["ticket_id"]
    conn = db.get_connection()
    conn.execute("UPDATE tickets SET status = 'assigned' WHERE ticket_id = ?", (ticket_id,))
    conn.commit()
    conn.close()
    db.init_db()
    assert _row(ticket_id)["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Email-style replies
# ---------------------------------------------------------------------------
def test_human_route_ticket_gets_agent_will_reply_by_email_message(client):
    headers, _ = _customer(client, name="Dana Smith")
    ticket_id = _raise(client, headers, _HUMAN)["ticket_id"]
    messages = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()["messages"]
    assert [m["sender"] for m in messages] == ["customer", "ai"]
    reply = messages[1]["message"]
    assert reply.startswith("Hi Dana,")
    assert "reply to you by email" in reply
    assert reply.endswith("Regards,\nSite24x7 Support Team")


def test_ai_replies_on_tickets_are_emailed_when_configured(client, sent_emails):
    headers, email = _customer(client)
    ticket_id = _raise(client, headers, _ai())["ticket_id"]
    assert len(sent_emails) == 1
    to, subject, body = sent_emails[0]
    assert to == email
    assert subject == f"Re: [{ticket_id}] Agent not reporting"
    assert body.startswith("Hi Dana,")


def test_chat_replies_are_never_emailed(client, sent_emails):
    headers, _ = _customer(client)
    _start_chat(client, headers, _ai("Here's how..."))
    assert sent_emails == []


def test_send_email_is_a_silent_noop_without_smtp(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    assert email_utils.send_email("a@example.com", "s", "b") is False


def test_pipeline_failure_still_creates_an_open_ticket(client):
    headers, _ = _customer(client)
    created = _raise(client, headers, RuntimeError("LLM down"))
    assert created["status"] == "open"
    row = _row(created["ticket_id"])
    assert row["route"] == "human"
    queue_ids = [t["ticket_id"] for t in client.get(
        "/api/staff/tickets",
        headers={"Authorization": "Bearer " + client.post(
            "/api/auth/login",
            json={"email": "technician@example.com", "password": "technician-demo-pass"},
        ).json()["token"]},
    ).json()]
    assert created["ticket_id"] in queue_ids


def test_generate_response_wraps_email_replies(monkeypatch):
    captured = []

    def _fake_complete(messages, **kwargs):
        captured.append(messages)
        return "Restart the agent service and check the proxy settings."

    monkeypatch.setattr(provider, "complete", _fake_complete)
    state = {"ticket_text": "agent not reporting", "category": "AGENT", "kb_candidates": [],
             "order_lookup": None, "reply_format": "email", "customer_name": "Dana Smith"}
    reply = graph.generate_response(state)["ai_response"]
    assert reply.startswith("Hi Dana,\n\nRestart the agent service")
    assert reply.endswith("Regards,\nSite24x7 Support Team")
    assert "Write ONLY the body" in captured[0][0]["content"]

    state["reply_format"] = "chat"
    assert graph.generate_response(state)["ai_response"] == (
        "Restart the agent service and check the proxy settings."
    )


# ---------------------------------------------------------------------------
# My tickets list + thread detail
# ---------------------------------------------------------------------------
def test_my_tickets_lists_tickets_and_escalated_chats_only(client):
    headers, _ = _customer(client)
    ticket = _raise(client, headers, _ai())
    _start_chat(client, headers, _ai("Here's how..."))
    escalated_chat = _start_chat(client, headers, _HUMAN, text="cancel my licence")

    listed = client.get("/api/tickets/mine", headers=headers).json()
    ids = [t["ticket_id"] for t in listed]
    assert ids == [escalated_chat["ticket_id"], ticket["ticket_id"]]  # newest first
    item = listed[1]
    assert item["subject"] == "Agent not reporting"
    assert item["ticket_type"] == "ticket"
    assert "confidence" not in item and "ticket_text" not in item


def test_unread_dot_clears_when_ticket_is_opened(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _ai())["ticket_id"]
    (item,) = client.get("/api/tickets/mine", headers=headers).json()
    assert item["unread"] is True
    client.get(f"/api/tickets/{ticket_id}", headers=headers)
    (item,) = client.get("/api/tickets/mine", headers=headers).json()
    assert item["unread"] is False


def test_thread_detail_has_sender_roles_and_to_lines(client):
    headers, _ = _customer(client, name="Dana Smith")
    ticket_id = _raise(client, headers, _ai())["ticket_id"]
    detail = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()
    assert detail["ticket"]["subject"] == "Agent not reporting"
    assert detail["ticket"]["category"] == "AGENT"
    customer_msg, ai_msg = detail["messages"]
    assert (customer_msg["sender_name"], customer_msg["sender_role"], customer_msg["to"]) == (
        "Dana Smith", "Customer", "Site24x7 Support")
    assert (ai_msg["sender_role"], ai_msg["to"]) == ("AI Assistant", "Dana Smith")


def test_customer_cannot_read_someone_elses_ticket(client):
    owner, _ = _customer(client)
    other, _ = _customer(client)
    ticket_id = _raise(client, owner, _ai())["ticket_id"]
    assert client.get(f"/api/tickets/{ticket_id}", headers=other).status_code == 403


# ---------------------------------------------------------------------------
# Reply + reopen
# ---------------------------------------------------------------------------
def test_reply_on_human_ticket_is_appended_without_rerunning_pipeline(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _HUMAN)["ticket_id"]
    runtime.set_graph(_StubGraph([]))  # any invoke would fail
    res = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "Extra detail"}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["route"] == "human"
    messages = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()["messages"]
    assert messages[-1]["message"] == "Extra detail"


def test_reply_on_pending_customer_ticket_reruns_pipeline_with_email_format(client):
    headers, _ = _customer(client)
    clarify = {"route": "clarify", "category": "ALERTS", "confidence": 0.5,
               "ai_response": "Which alert?", "clarification_attempts": 1}
    ticket_id = _raise(client, headers, clarify)["ticket_id"]
    runtime.set_graph(_StubGraph([_ai("Hi Dana,\n\nDo X.\n\nRegards,\nSite24x7 Support Team")]))
    res = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "The email alert"},
                      headers=headers)
    assert res.json()["status"] == "resolved"
    assert runtime.get_graph().invocations[0]["reply_format"] == "email"


def test_reopen_ai_resolved_ticket_escalates_to_open(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _ai())["ticket_id"]
    res = client.post(f"/api/tickets/{ticket_id}/reopen", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "open"
    row = _row(ticket_id)
    assert row["route"] == "human"
    assert "CASE SUMMARY" in row["ticket_text"]


def test_only_resolved_tickets_can_be_reopened(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, _HUMAN)["ticket_id"]
    assert client.post(f"/api/tickets/{ticket_id}/reopen", headers=headers).status_code == 400


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
def test_upload_and_download_attachments(client, tmp_path):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    res = _upload(client, headers, created["ticket_id"], created["message_id"],
                  [("screen.png", PNG), ("report.pdf", PDF), ("agent.log", TXT)])
    assert res.status_code == 200, res.text
    uploaded = res.json()
    assert [a["content_type"] for a in uploaded] == ["image/png", "application/pdf", "text/plain"]

    # Stored next to the DB, under random names.
    stored = sorted(p.name for p in (tmp_path / "uploads").iterdir())
    assert len(stored) == 3 and "screen.png" not in stored

    detail = client.get(f"/api/tickets/{created['ticket_id']}", headers=headers).json()
    assert [a["filename"] for a in detail["messages"][0]["attachments"]] == [
        "screen.png", "report.pdf", "agent.log"]

    download = client.get(
        f"/api/tickets/{created['ticket_id']}/attachments/{uploaded[2]['attachment_id']}",
        headers=headers,
    )
    assert download.status_code == 200
    assert download.content == TXT
    assert "agent.log" in download.headers["content-disposition"]


def test_more_than_five_attachments_rejected(client):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    files = [(f"f{i}.txt", TXT) for i in range(6)]
    res = _upload(client, headers, created["ticket_id"], created["message_id"], files)
    assert res.status_code == 400
    # Limit is per message, across uploads.
    assert _upload(client, headers, created["ticket_id"], created["message_id"], files[:4]).status_code == 200
    assert _upload(client, headers, created["ticket_id"], created["message_id"], files[:2]).status_code == 400


def test_oversized_attachment_rejected(client):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    big = b"a" * (10 * 1024 * 1024 + 1)
    res = _upload(client, headers, created["ticket_id"], created["message_id"], [("big.txt", big)])
    assert res.status_code == 413


@pytest.mark.parametrize("name,data", [
    ("run.exe", b"MZ\x90\x00"),
    ("page.html", b"<script>alert(1)</script>"),
    ("fake.png", b"not really a png"),
    ("fake.pdf", PNG),
    ("binary.txt", b"abc\x00def"),
])
def test_disallowed_or_mismatched_attachments_rejected(client, name, data):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    res = _upload(client, headers, created["ticket_id"], created["message_id"], [(name, data)])
    assert res.status_code == 400


def test_one_bad_file_rejects_the_whole_upload(client, tmp_path):
    headers, _ = _customer(client)
    created = _raise(client, headers, _ai())
    res = _upload(client, headers, created["ticket_id"], created["message_id"],
                  [("ok.png", PNG), ("bad.exe", b"MZ")])
    assert res.status_code == 400
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_attachments_only_on_own_ticket_and_own_messages(client):
    owner, _ = _customer(client)
    other, _ = _customer(client)
    created = _raise(client, owner, _ai())
    ticket_id = created["ticket_id"]
    assert _upload(client, other, ticket_id, created["message_id"], [("a.txt", TXT)]).status_code == 403

    ai_message_id = client.get(f"/api/tickets/{ticket_id}", headers=owner).json()["messages"][1]["message_id"]
    assert _upload(client, owner, ticket_id, ai_message_id, [("a.txt", TXT)]).status_code == 400
    assert _upload(client, owner, ticket_id, "MSG-nope", [("a.txt", TXT)]).status_code == 404

    uploaded = _upload(client, owner, ticket_id, created["message_id"], [("a.txt", TXT)]).json()
    url = f"/api/tickets/{ticket_id}/attachments/{uploaded[0]['attachment_id']}"
    assert client.get(url, headers=other).status_code == 403
    assert client.get(url).status_code == 401
