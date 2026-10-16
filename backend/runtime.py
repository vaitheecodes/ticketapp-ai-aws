"""Holder for the compiled LangGraph pipeline, built once at startup
(backend/main.py) and shared by the ticket and chat routes. Tests swap
in a stub with set_graph()."""

from __future__ import annotations

_graph = None


def set_graph(graph) -> None:
    global _graph
    _graph = graph


def get_graph():
    if _graph is None:
        raise RuntimeError("Pipeline not initialised; the app startup hook builds it")
    return _graph
