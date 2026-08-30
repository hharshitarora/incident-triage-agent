"""Thin LLM boundary: one function that returns a validated pydantic object.

Structured output + retries live here so nodes never touch raw model text.
"""
from __future__ import annotations

from typing import Type, TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import load_config

T = TypeVar("T", bound=BaseModel)

_clients: dict[bool, ChatOpenAI] = {}


def _client(cheap: bool) -> ChatOpenAI:
    if cheap not in _clients:
        cfg = load_config()
        _clients[cheap] = ChatOpenAI(
            model=cfg.model_cheap if cheap else cfg.model,
            api_key=cfg.openai_api_key,
            temperature=0,
        )
    return _clients[cheap]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def structured(prompt: str, schema: Type[T], *, cheap: bool = False) -> T:
    """Invoke the model and return a validated `schema` instance.

    Retries with backoff on transient API/validation failures.
    """
    llm = _client(cheap).with_structured_output(schema)
    return llm.invoke(prompt)  # type: ignore[return-value]
