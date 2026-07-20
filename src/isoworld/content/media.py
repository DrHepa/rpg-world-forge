from __future__ import annotations

from pathlib import Path

from isoworld.runtime_io import RuntimeIOError, read_json_object


def media_signature_matches(path: Path, media_type: str) -> bool:
    """Validate the declared runtime media type from bytes, not its extension."""

    try:
        with path.open("rb") as source:
            head = source.read(16)
        if media_type == "image/png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/jpeg":
            return head.startswith(b"\xff\xd8\xff")
        if media_type == "image/webp":
            return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        if media_type == "audio/wav":
            return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
        if media_type == "audio/ogg":
            return head.startswith(b"OggS")
        if media_type == "audio/mpeg":
            return head.startswith(b"ID3") or (
                len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0
            )
        if media_type == "font/ttf":
            return head.startswith((b"\x00\x01\x00\x00", b"true"))
        if media_type == "font/otf":
            return head.startswith(b"OTTO")
        if media_type == "application/json":
            read_json_object(path)
            return True
        if media_type == "text/x-glsl":
            text = path.read_text(encoding="utf-8")
            return "\x00" not in text and bool(text.strip())
    except (OSError, UnicodeDecodeError, RuntimeIOError):
        return False
    return False
