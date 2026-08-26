"""Shared path normalization helper.

Governance Controller path comparisons (forbidden paths, scope checks) need a
single, deterministic normalization so that ``..`` / ``~`` segments cannot be
used to bypass prefix checks.
"""

from __future__ import annotations

import os


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
