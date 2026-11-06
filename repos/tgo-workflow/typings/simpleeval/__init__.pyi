"""Signature used by Workflow, checked against simpleeval 0.9.13 source."""

from ast import AST, Name
from collections.abc import Callable, Mapping

def simple_eval(
    expr: str,
    operators: Mapping[type[AST], Callable[..., object]] | None = ...,
    functions: Mapping[str, Callable[..., object]] | None = ...,
    names: Mapping[str, object] | Callable[[Name], object] | None = ...,
) -> object: ...
