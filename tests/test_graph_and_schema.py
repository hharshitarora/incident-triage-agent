import pytest
from pydantic import ValidationError

from triage.graph import build_graph
from triage.state import ParsedError, TriageReport


def test_graph_compiles():
    assert build_graph() is not None


def test_confidence_is_bounded():
    with pytest.raises(ValidationError):
        TriageReport(
            summary="s", root_cause_hypothesis="r", confidence=1.5, severity="high"
        )


def test_parsed_error_defaults_are_empty():
    e = ParsedError(error_type="KeyError", message="m", signature="sig")
    assert e.stack_frames == []
    assert e.implicated_files == []
