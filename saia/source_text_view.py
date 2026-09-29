"""Offset-preserving view of audited, escaped metadata; raw evidence stays intact."""

from __future__ import annotations

import re


VERSION = "whitespace-adjacent-escaped-controls-v1"
# Do not blindly unicode_escape scientific text: LaTeX commands and Unicode
# would be corrupted. Escapes attached to following words remain unresolved.
ESCAPED_CONTROL = re.compile(r"(?<!\\)\\[nrt](?=\s|$)")


def source_text_view(text: str, mode: str = "literal") -> str:
    if not isinstance(text, str):
        raise ValueError("Source text must be a string")
    if mode == "literal":
        return text
    if mode != VERSION:
        raise ValueError("Unsupported source text view")
    return ESCAPED_CONTROL.sub(lambda match: " " * len(match.group()), text)
