"""Streamlit UI for the ticket-resolution agent.

Run with: streamlit run src/ui/app.py
"""

from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ on sys.path

from agent import state as routing_state  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.run_demo import DUMMY_TICKETS  # noqa: E402

st.set_page_config(page_title="Ticket Resolution Agent", page_icon="🎫", layout="wide")


@st.cache_resource
def get_graph():
    return build_graph()


def random_id(prefix: str, n: int = 6) -> str:
    return f"{prefix}-{''.join(random.choices(string.digits, k=n))}"


def render_result(result: dict) -> None:
    st.info("Demo mode — stub classifier, no live Bedrock calls yet.", icon="ℹ️")

    col1, col2, col3 = st.columns(3)
    col1.metric("Intent", result["intent"])
    col2.metric("Category", result["category"])
    col3.metric("Confidence", f"{result['confidence']:.0%}")

    if result["route"] == "auto_resolve":
        st.success(f"**Route: auto-resolve** — {result['route_reason']}")
        st.markdown("**Response to customer:**")
        st.write(result["response_text"])
    else:
        st.warning(f"**Route: escalate** — {result['route_reason']}")
        st.markdown("**Escalation summary (handoff to human agent):**")
        summary = result["escalation_summary"]
        with st.container(border=True):
            st.markdown(f"**Ticket:** `{summary['ticket_id']}`")
            st.markdown(
                f"**Intent:** {summary['intent']}  ·  "
                f"**Confidence:** {summary['confidence']:.0%}"
            )
            st.markdown(f"**Customer message:** {summary['instruction_text']}")
            st.markdown(f"**Reason for escalation:** {summary['reason']}")

    with st.expander("Node trace"):
        trace = result.get("trace", [])
        st.write(" → ".join(trace))
        for i, node in enumerate(trace, start=1):
            st.markdown(f"{i}. `{node}`")


def run_ticket(ticket: dict) -> None:
    graph = get_graph()
    result = graph.invoke(ticket)
    render_result(result)


# --- sidebar: routing policy explainer, read live off agent.state ----------
with st.sidebar:
    st.header("Routing policy")
    st.caption("Read live from `agent.state` — can't drift out of sync with the code.")

    st.metric("Base confidence threshold", f"{routing_state.CONFIDENCE_THRESHOLD:.0%}")
    st.metric(
        "High-risk confidence threshold",
        f"{routing_state.HIGH_RISK_CONFIDENCE_THRESHOLD:.0%}",
    )

    st.markdown("**High-risk categories**")
    st.write(", ".join(sorted(routing_state.HIGH_RISK_CATEGORIES)))

    st.markdown("**Escalation language tags**")
    st.write(", ".join(sorted(routing_state.ESCALATION_LANGUAGE_TAGS)))

    st.divider()
    st.markdown(
        "Decision order, checked in `decide_route`:\n"
        "1. category risk\n"
        "2. escalation language tags\n"
        "3. base confidence threshold"
    )

# --- main area ---------------------------------------------------------
st.title("\U0001f3ab Ticket Resolution Agent")
st.caption(
    "LangGraph agent — classifies an incoming ticket, then routes it to "
    "auto-resolve or human escalation."
)

tab_submit, tab_demo = st.tabs(["Submit a ticket", "Try the demo tickets"])

with tab_submit:
    with st.form("submit_ticket_form"):
        instruction_text = st.text_area(
            "Ticket text",
            placeholder="e.g. I want to track my order ORD-123456",
        )
        channel = st.selectbox("Channel", ["chat", "email", "voice"])
        customer_id = st.text_input(
            "Customer ID (optional)", placeholder="leave blank to auto-generate"
        )
        submitted = st.form_submit_button("Submit ticket")

    if submitted:
        if not instruction_text.strip():
            st.error("Enter some ticket text first.")
        else:
            ticket = {
                "ticket_id": random_id("TCK-UI"),
                "channel": channel,
                "customer_id": customer_id.strip() or random_id("CUST"),
                "instruction_text": instruction_text.strip(),
                "language_tags": [],
            }
            run_ticket(ticket)

with tab_demo:
    st.caption(
        "Pick one of the 6 tickets already defined in `run_demo.py` — "
        "always a working example, even with a blank text field."
    )
    labels = [
        f"{t['ticket_id']} — {t['instruction_text'][:60]}" for t in DUMMY_TICKETS
    ]
    choice = st.radio("Demo ticket", labels, label_visibility="collapsed")
    selected = DUMMY_TICKETS[labels.index(choice)]
    st.text_area(
        "Ticket text", value=selected["instruction_text"], disabled=True, key="demo_preview"
    )
    if st.button("Run this ticket"):
        run_ticket(selected)
