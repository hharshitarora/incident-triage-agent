"""Graph wiring. Each phase is a separate node, so any one can be swapped,
tested, or re-ordered without touching the others — the whole point of using an
orchestrator instead of one monolithic agent loop.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.git_history import git_history
from .nodes.issue_tracker import issue_tracker
from .nodes.log_search import log_search
from .nodes.report import report
from .state import TriageState


def build_graph():
    g = StateGraph(TriageState)
    g.add_node("log_search", log_search)
    g.add_node("git_history", git_history)
    g.add_node("issue_tracker", issue_tracker)
    g.add_node("report", report)

    g.add_edge(START, "log_search")
    g.add_edge("log_search", "git_history")
    g.add_edge("git_history", "issue_tracker")
    g.add_edge("issue_tracker", "report")
    g.add_edge("report", END)

    return g.compile()
