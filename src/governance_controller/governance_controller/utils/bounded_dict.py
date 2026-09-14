"""Shared depth/size bound for untyped ``dict[str, Any]`` contract fields.

``TaskContract.verification``/``opentasks_dag`` and ``ProjectProfile.llm``/
``audit`` are free-form dicts with no structural limit, unlike this codebase's
other bounded string/list fields (see ``utils.paths.reject_root_prefixes``).
A sufficiently deep payload passes validation and only fails later, inside
``model_dump(mode="json")``, where pydantic-core's own circular-reference
guard raises an unhandled ``ValueError`` (#377) -- a 500 where a 422 belongs.
This validator rejects such payloads at the schema boundary instead.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import field_validator

MAX_NESTING_DEPTH = 20
MAX_SERIALIZED_BYTES = 32_768


def _depth(value: Any, *, budget: int) -> int:
    """Return the nesting depth of *value*, stopping early past *budget*.

    Recursion is bounded by *budget* (decremented each level), so this never
    recurses deeper than ``budget + 1`` regardless of how deep *value*
    actually nests -- a pathological input can't make the check itself slow
    or blow the Python recursion limit.
    """
    if not isinstance(value, (dict, list)):
        return 0
    if budget <= 0:
        return budget + 1
    children = value.values() if isinstance(value, dict) else value
    return 1 + max((_depth(child, budget=budget - 1) for child in children), default=0)


def _check_bounded_dict(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    if _depth(value, budget=MAX_NESTING_DEPTH) > MAX_NESTING_DEPTH:
        raise ValueError(
            f"{field_name} exceeds maximum nesting depth of {MAX_NESTING_DEPTH}"
        )
    try:
        size = len(json.dumps(value))
    except TypeError as exc:
        raise ValueError(f"{field_name} is not JSON-serializable") from exc
    if size > MAX_SERIALIZED_BYTES:
        raise ValueError(
            f"{field_name} exceeds maximum serialized size of "
            f"{MAX_SERIALIZED_BYTES} bytes"
        )
    return value


def bounded_dict_field(field_name: str):  # type: ignore[no-untyped-def]
    """Return a Pydantic field validator bounding depth/size of *field_name*.

    ``value`` is left untouched when it isn't a ``dict`` (e.g. ``None`` for an
    optional field) -- structural type-checking is pydantic's job, not this
    validator's.
    """

    @field_validator(field_name, mode="after")
    def _validator(cls, value):  # type: ignore[no-untyped-def]
        if not isinstance(value, dict):
            return value
        return _check_bounded_dict(value, field_name=field_name)

    return _validator
