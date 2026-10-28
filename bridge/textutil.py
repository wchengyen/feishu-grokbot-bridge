"""Split long Feishu replies. Feishu text messages stay well under the API limit."""

from __future__ import annotations


def split_text(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters."""

    if limit < 1:
        raise ValueError("limit must be positive")
    if text == "":
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        piece = remaining[:cut].rstrip("\n")
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].lstrip("\n")
        if not piece and cut == 0:
            # Degenerate input; force progress.
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
    return chunks


def delivery_plan(text: str, *, split_at: int, file_at: int) -> tuple[str, list[str]]:
    """Return ``("messages", chunks)`` or ``("file", [preview])``.

    Texts longer than ``file_at`` are uploaded as one markdown file. The preview
    is what the chat message shows beside that file.
    """

    if len(text) > file_at:
        preview = text[:split_at].rstrip()
        if len(text) > split_at:
            preview = preview.rstrip() + "\n…（全文已上傳為檔案）"
        return "file", [preview] if preview else []
    return "messages", split_text(text, split_at)
