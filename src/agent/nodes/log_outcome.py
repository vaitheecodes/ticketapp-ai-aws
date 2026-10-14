"""log_outcome — terminal node.

Stage 1: just closes out the trace. Stage 2+ will write the result to a
`resolutions_ai` table (mirroring the real `resolutions` schema — see
src/pipeline/03_gold_load.py) so deflection-rate and quality metrics can
be computed against it in notebooks/eval.ipynb.
"""

from __future__ import annotations

from agent.state import TicketState


def log_outcome(state: TicketState) -> dict:
    return {
        "trace": [*state.get("trace", []), "log_outcome"],
    }
