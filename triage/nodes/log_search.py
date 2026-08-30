"""Phase 1 — Log Search: turn a raw error log into a structured ParsedError."""
from __future__ import annotations

from ..llm import structured
from ..observability import span
from ..state import ParsedError, TriageState

_PROMPT = """You are a production incident-triage assistant. Parse the error log below.

Extract:
- error_type: the exception class or error category
- message: the human-readable error message
- signature: a SHORT, STABLE signature good for dedup/search (strip line numbers,
  timestamps, memory addresses, and request ids)
- stack_frames: file / line / function for each frame you can see
- implicated_files: the repo-relative source files most likely at fault

Only use information present in the log. Leave a field empty if it is not present.

LOG:
{log}
"""


def log_search(state: TriageState) -> dict:
    with span("log_search"):
        log = state["log_text"][:8000]
        parsed = structured(_PROMPT.format(log=log), ParsedError, cheap=True)
        note = (
            f"log_search: {parsed.error_type} · "
            f"{len(parsed.stack_frames)} frames · "
            f"{len(parsed.implicated_files)} files"
        )
        return {"parsed_error": parsed, "trace": state.get("trace", []) + [note]}
