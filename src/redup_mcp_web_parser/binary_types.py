"""Binary type detection (magic / Content-Type / extension)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Popular downloadable binaries for agents (docs, archives, images).
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        "pdf",
        "zip",
        "7z",
        "rar",
        "tar",
        "gz",
        "tgz",
        "bz2",
        "xz",
        "docx",
        "xlsx",
        "pptx",
        "doc",
        "xls",
        "ppt",
        "odt",
        "ods",
        "odp",
        "epub",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "ico",
        "svg",  # often served as file download; not HTML article body
        "wasm",
        "dmg",
        "exe",
        "bin",
    }
)

_BINARY_CTYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/zip",
        "application/x-zip-compressed",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
        "application/vnd.rar",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-bzip2",
        "application/x-xz",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/epub+zip",
        "application/octet-stream",
        "application/wasm",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/svg+xml",
    }
)

_PDF_PATH_RE = re.compile(r"/pdf(/|$)", re.IGNORECASE)
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,8})(\?|#|$)")


def _ctype(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def url_extension(url: str) -> str:
    path = urlparse((url or "").strip()).path
    match = _EXT_RE.search(path)
    return match.group(1).lower() if match else ""


def url_has_binary_extension(url: str) -> bool:
    return url_extension(url) in BINARY_EXTENSIONS


def url_suggests_binary(url: str) -> bool:
    """Soft URL heuristic (extension or ``/pdf/`` path). Not enough alone."""
    raw = (url or "").strip()
    if not raw:
        return False
    if url_has_binary_extension(raw):
        return True
    return bool(_PDF_PATH_RE.search(urlparse(raw).path))


def is_binary_content_type(content_type: str | None) -> bool:
    ctype = _ctype(content_type)
    if not ctype:
        return False
    if ctype in _BINARY_CTYPES:
        return True
    if ctype.startswith("image/") and ctype not in {"image/svg+xml"}:
        return True
    return ctype.startswith(("audio/", "video/", "font/"))


def is_clearly_textual_content_type(content_type: str | None) -> bool:
    """HTML/JSON/XML/text — use parse_page, not fetch_binary."""
    ctype = _ctype(content_type)
    if not ctype or ctype == "application/octet-stream":
        return False
    if is_binary_content_type(ctype):
        return False
    if ctype.startswith("text/"):
        return True
    if ctype in {
        "application/json",
        "application/javascript",
        "application/xml",
        "application/xhtml+xml",
        "application/atom+xml",
        "application/rss+xml",
        "application/ld+json",
    }:
        return True
    return "html" in ctype or "xml" in ctype or "json" in ctype


def detect_kind_from_magic(body_prefix: bytes) -> str | None:
    data = body_prefix or b""
    if data.lstrip().startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        return "zip"
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.startswith(b"BZh"):
        return "bzip2"
    if data.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if data.startswith(b"7z\xbc\xaf'\x1c"):
        return "7z"
    if data.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if len(data) >= 262 and data[257:262] == b"ustar":
        return "tar"
    if data.startswith(b"\x00asm"):
        return "wasm"
    return None


def refine_zip_kind(url: str, content_type: str | None, kind: str) -> str:
    """Map generic zip/ole to a more specific kind using URL / Content-Type."""
    ext = url_extension(url)
    ctype = _ctype(content_type)
    if kind == "zip":
        for candidate in (
            "docx",
            "xlsx",
            "pptx",
            "odt",
            "ods",
            "odp",
            "epub",
            "jar",
        ):
            if ext == candidate or candidate in ctype:
                return candidate
        if "wordprocessingml" in ctype:
            return "docx"
        if "spreadsheetml" in ctype:
            return "xlsx"
        if "presentationml" in ctype:
            return "pptx"
        if "opendocument.text" in ctype:
            return "odt"
        if "epub" in ctype:
            return "epub"
        return "zip"
    if kind == "ole":
        if ext in {"doc", "xls", "ppt"}:
            return ext
        if "msword" in ctype:
            return "doc"
        if "excel" in ctype:
            return "xls"
        if "powerpoint" in ctype:
            return "ppt"
        return "ole"
    return kind


def kind_from_content_type(content_type: str | None) -> str | None:
    ctype = _ctype(content_type)
    mapping = {
        "application/pdf": "pdf",
        "application/zip": "zip",
        "application/x-zip-compressed": "zip",
        "application/x-7z-compressed": "7z",
        "application/x-rar-compressed": "rar",
        "application/vnd.rar": "rar",
        "application/gzip": "gzip",
        "application/x-gzip": "gzip",
        "application/x-tar": "tar",
        "application/x-bzip2": "bzip2",
        "application/x-xz": "xz",
        "application/msword": "doc",
        "application/vnd.ms-excel": "xls",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.oasis.opendocument.text": "odt",
        "application/vnd.oasis.opendocument.spreadsheet": "ods",
        "application/vnd.oasis.opendocument.presentation": "odp",
        "application/epub+zip": "epub",
        "application/wasm": "wasm",
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/x-icon": "ico",
        "image/vnd.microsoft.icon": "ico",
        "image/svg+xml": "svg",
    }
    if ctype in mapping:
        return mapping[ctype]
    if ctype.startswith("image/"):
        return ctype.split("/", 1)[-1] or "image"
    if ctype == "application/octet-stream":
        return "bin"
    return None


def decide_binary(
    *,
    url: str,
    content_type: str | None,
    body_prefix: bytes = b"",
) -> tuple[bool, str]:
    """Return ``(is_binary, kind)`` for parse_page reject / fetch validation."""
    magic = detect_kind_from_magic(body_prefix)
    if magic:
        return True, refine_zip_kind(url, content_type, magic)

    if is_clearly_textual_content_type(content_type):
        return False, ""

    if is_binary_content_type(content_type):
        kind = kind_from_content_type(content_type) or url_extension(url) or "bin"
        if kind == "zip":
            kind = refine_zip_kind(url, content_type, "zip")
        return True, kind

    # Ambiguous / missing type: trust known binary extensions only.
    ext = url_extension(url)
    if ext in BINARY_EXTENSIONS:
        return True, ext
    return False, ""
