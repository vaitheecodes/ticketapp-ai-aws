"""Per-task Bedrock model: BEDROCK_MODEL_ID_CHAT overrides BEDROCK_MODEL_ID
for the customer-facing chat answer only. Fake client: no AWS calls."""

from __future__ import annotations

import pytest

from backend.ai_service import backends, categorize_ticket, chat_response, provider
from backend.ai_service.backends import BedrockBackend
from backend.ai_service.schemas import AnswerInput, CategorizeInput, ClarifyInput
from tests.test_bedrock_conversation import _REAL_COMPLETE, _StrictClient

LITE, PRO = "apac.amazon.nova-lite-v1:0", "apac.amazon.nova-pro-v1:0"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(provider, "complete", _REAL_COMPLETE)
    fake = _StrictClient()
    provider.set_backend(BedrockBackend(client=fake))
    return fake


def _models(fake):
    return [call["modelId"] for call in fake.calls]


def test_default_is_bedrock_model_id(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID_CHAT", raising=False)
    assert BedrockBackend.model_id() == LITE
    with backends.model_task("chat"):
        assert BedrockBackend.model_id() == LITE


def test_chat_override_applies_only_inside_the_chat_task(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID_CHAT", PRO)
    assert BedrockBackend.model_id() == LITE
    with backends.model_task("chat"):
        assert BedrockBackend.model_id() == PRO
    assert BedrockBackend.model_id() == LITE  # reset afterwards
    monkeypatch.setenv("BEDROCK_MODEL_ID_CHAT", "  ")
    with backends.model_task("chat"):
        assert BedrockBackend.model_id() == LITE  # blank: not set


def test_only_the_chat_answer_uses_the_chat_model(monkeypatch, client):
    monkeypatch.setenv("BEDROCK_MODEL_ID_CHAT", PRO)
    categorize_ticket.categorize_ticket(CategorizeInput(ticket_text="My Linux agent stopped sending data"))
    chat_response.answer(AnswerInput(ticket_text="Customer: how do I install the agent"))
    chat_response.clarify(ClarifyInput(ticket_text="Customer: it is broken", category="MONITORING"))
    chat_response.answer(AnswerInput(ticket_text="how do I install the agent", reply_format="email",
                                     customer_name="Dana"))
    assert _models(client) == [LITE, PRO, LITE, LITE]


def test_a_regenerated_chat_answer_stays_on_the_chat_model(monkeypatch, client):
    monkeypatch.setenv("BEDROCK_MODEL_ID_CHAT", PRO)
    client.text = "[Your answer here]"  # rejected by the chat filter, then regenerated
    chat_response.answer(AnswerInput(ticket_text="Customer: how do I install the agent"))
    assert _models(client) == [PRO, PRO]


def test_without_the_override_everything_uses_the_default(monkeypatch, client):
    monkeypatch.delenv("BEDROCK_MODEL_ID_CHAT", raising=False)
    categorize_ticket.categorize_ticket(CategorizeInput(ticket_text="My Linux agent stopped sending data"))
    chat_response.answer(AnswerInput(ticket_text="Customer: how do I install the agent"))
    assert _models(client) == [LITE, LITE]
