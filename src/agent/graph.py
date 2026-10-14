"""LangGraph definition for ticket resolution.

Graph shape:

    ingest_ticket -> classify_intent -> retrieve_knowledge -> decide_route
                                                                    |
                                              +---------------------+----------------------+
                                              |                                             |
                                       (auto_resolve)                                 (escalate)
                                       generate_response                              escalate
                                              |                                             |
                                              +---------------------+----------------------+
                                                                    |
                                                               log_outcome -> END

Stage 1: every node is a stub (no Bedrock calls) so this shape and the
routing decision can be inspected on dummy data — see run_demo.py.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent.nodes.classify_intent import classify_intent
from agent.nodes.decide_route import decide_route, route_after_decide
from agent.nodes.escalate import escalate
from agent.nodes.generate_response import generate_response
from agent.nodes.ingest_ticket import ingest_ticket
from agent.nodes.log_outcome import log_outcome
from agent.nodes.retrieve_knowledge import retrieve_knowledge
from agent.state import TicketState


def build_graph():
    graph = StateGraph(TicketState)

    graph.add_node("ingest_ticket", ingest_ticket)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve_knowledge", retrieve_knowledge)
    graph.add_node("decide_route", decide_route)
    graph.add_node("generate_response", generate_response)
    graph.add_node("escalate", escalate)
    graph.add_node("log_outcome", log_outcome)

    graph.set_entry_point("ingest_ticket")
    graph.add_edge("ingest_ticket", "classify_intent")
    graph.add_edge("classify_intent", "retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "decide_route")

    graph.add_conditional_edges(
        "decide_route",
        route_after_decide,
        {
            "auto_resolve": "generate_response",
            "escalate": "escalate",
        },
    )

    graph.add_edge("generate_response", "log_outcome")
    graph.add_edge("escalate", "log_outcome")
    graph.add_edge("log_outcome", END)

    return graph.compile()
