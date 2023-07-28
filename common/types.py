from __future__ import annotations

from typing import Literal, TypeAlias, Union, Annotated, overload
from pydantic import errors, ValidationError, PositiveInt, Field, TypeAdapter
from pydantic.types import constr


MixedCaseIdentifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9_]+$')]
Identifier = Annotated[str, Field(pattern=r'^[a-z0-9_]+$')]


MixedCaseIdentifierAdapter = TypeAdapter(MixedCaseIdentifier)
IdentifierAdapter = TypeAdapter(Identifier)


@overload
def validate_identifier(s: str, mixed: Literal[True]) -> MixedCaseIdentifier: ...

@overload
def validate_identifier(s: str, mixed: Literal[False] = ...) -> Identifier: ...


def validate_identifier(s: str, mixed: bool = False):
    if mixed:
        return MixedCaseIdentifierAdapter.validate_python(s)
    else:
        return IdentifierAdapter.validate_python(s)
