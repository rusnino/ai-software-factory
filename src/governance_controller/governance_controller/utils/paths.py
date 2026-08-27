"""Shared path normalization helper.

Governance Controller path comparisons (forbidden paths, scope checks) need a
single, deterministic normalization so that ``..`` / ``~`` segments cannot be
used to bypass prefix checks.
"""

from __future__ import annotations

import os

from pydantic import field_validator


def _reject_root_prefixes(value: list[str]) -> list[str]:
    """Reject any path that normalizes to the filesystem root.

    This helper is used as a Pydantic field validator for forbidden/allowed path
    lists. Root-collapsing paths would match every real path as an empty-string
    prefix and therefore break the check intent.
    """
    for path in value:
        try:
            normalized = normalize_path(path)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if normalized == "/":
            raise ValueError(
                f"path normalizes to root and cannot be used as a prefix: {path!r}"
            )
    return value


def reject_root_prefixes(field_name: str):  # type: ignore[no-untyped-def]
    """Return a Pydantic field validator for *field_name* that blocks root paths."""

    @field_validator(field_name, mode="before")
    def _validator(cls, value):  # type: ignore[no-untyped-def]
        if not isinstance(value, list):
            return value
        return _reject_root_prefixes(value)

    return _validator


def normalize_path(path: str) -> str:
    """Return a normalized path for comparison.

    Expands ``~`` and resolves ``..`` segments using stdlib helpers. Relative
    paths are anchored at the root so that ``..`` segments cannot escape above
    an absolute forbidden prefix.

    Paths that collapse to the filesystem root (``.``, ``..``, ``/``) are
    rejected because they would match every path as an empty-string prefix.
    """
    expanded = os.path.expanduser(path)
    # Anchor relative paths at / so that normpath resolves .. without depending
    # on the current working directory.
    if not os.path.isabs(expanded):
        expanded = "/" + expanded
    normalized = os.path.normpath(expanded)
    root = normalized.rstrip("/")
    if root == "":
        raise ValueError(
            f"path normalizes to root and cannot be used as a prefix: {path!r}"
        )
    return root
