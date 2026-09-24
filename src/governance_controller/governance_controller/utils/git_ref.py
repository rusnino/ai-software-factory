"""Git ref-name validation shared between schema validation and git-argv call sites.

``git check-ref-format --branch`` is the authoritative grammar for a valid
branch name; this reimplements the security-relevant subset of it in Python
so it can run both as a cheap Pydantic validator (no subprocess) and as an
in-process guard immediately before a value is interpolated into a git argv
token.

The single most important rule is the leading-``-`` rejection: a bare argv
token that starts with ``-`` is indistinguishable from a command-line flag to
``git`` regardless of which revision/ref position it occupies, so any string
accepted here MUST NOT start with ``-``. Without this check, a value that
merely satisfies "is a string" validation lets an attacker inject arbitrary
git options into any subprocess call that interpolates it (NEXT-37 / GH #418).
"""

from __future__ import annotations

_CONTROL_CHARS = frozenset(chr(c) for c in range(0, 32)) | {chr(127)}
_INVALID_CHARS = frozenset(" ~^:?*[\\") | _CONTROL_CHARS


def is_valid_git_branch_name(name: str) -> bool:
    """Return ``True`` if *name* is safe to use as a git branch/ref-name argv token.

    Rejects everything ``git check-ref-format --branch`` would reject, plus
    the argument-injection vector a leading ``-`` opens (see module docstring).
    Intentionally conservative: a legitimate branch name never needs any of
    the rejected characters or shapes, so false positives here are harmless,
    while a false negative is a security hole.
    """
    if not name or not isinstance(name, str):
        return False
    if name.startswith("-"):
        return False
    if name == "@":
        return False
    if any(ch in _INVALID_CHARS for ch in name):
        return False
    if ".." in name or "@{" in name:
        return False
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return False
    if name.endswith(".") or name.endswith(".lock"):
        return False
    return not any(part.startswith(".") for part in name.split("/"))
