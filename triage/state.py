"""State + structured schemas that flow through the triage graph.

The graph state is a TypedDict so nodes can return partial updates; the payloads
are pydantic models so every LLM output is validated at the boundary.
"""
from __future__ import annotations

from typing import Optional, TypedDict

from pydantic import BaseModel, Field


class StackFrame(BaseModel):
    file: str
    line: Optional[int] = None
    function: Optional[str] = None


class ParsedError(BaseModel):
    error_type: str = Field(description="Exception/error class or category")
    message: str = Field(description="The human-readable error message")
    signature: str = Field(
        description="A short, stable signature for dedup/search (no line numbers/timestamps)"
    )
    stack_frames: list[StackFrame] = Field(default_factory=list)
    implicated_files: list[str] = Field(
        default_factory=list,
        description="Source files most likely involved, repo-relative paths",
    )


class SuspectCommit(BaseModel):
    sha: str
    author: str
    date: str
    summary: str
    files: list[str] = Field(default_factory=list)
    patch: str = Field(default="", description="Truncated diff, only for prime suspects")
    why: str = ""


class RelatedIssue(BaseModel):
    number: int
    title: str
    url: str
    state: str


class TriageReport(BaseModel):
    summary: str = Field(description="One or two sentence executive summary")
    root_cause_hypothesis: str
    confidence: float = Field(ge=0.0, le=1.0, description="Calibrated 0-1 confidence")
    suspect_commit: Optional[str] = Field(
        default=None, description="SHA of the single most likely culprit, or null"
    )
    severity: str = Field(description="one of: low, medium, high, critical")
    recommended_owner: Optional[str] = None
    related_issue_numbers: list[int] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class TriageState(TypedDict, total=False):
    # --- inputs ---
    repo_path: str
    log_text: str
    github_repo: Optional[str]  # "owner/name"
    # --- node outputs ---
    parsed_error: ParsedError
    suspect_commits: list[SuspectCommit]
    related_issues: list[RelatedIssue]
    report: TriageReport
    # --- meta (accumulated) ---
    trace: list[str]
    warnings: list[str]
