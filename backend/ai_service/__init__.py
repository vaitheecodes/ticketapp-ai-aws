"""Modular AI service: one module per operation, each with pydantic
input/output schemas, so routes and the LangGraph agent never parse raw
model text themselves.

Sub-stage A introduces the provider wrapper and the chat summaries
(summaries.py); the remaining operations move here from backend/graph.py
in sub-stage C.
"""

from backend.ai_service.provider import AIError  # noqa: F401
