"""CSV utilities for Optipro/Sogis exports.

Handles:
- Encoding detection (UTF-8 / Latin-1 / CP1252 fallback)
- Separator detection (`;` or `,` or tab)
- Header normalization (strip BOM, lower, replace accents)
- Preview generation (first N rows + sniffed metadata)
- Number parsing (French `1.234,56` vs English `1,234.56`)
- Date parsing (`DD/MM/YYYY`)
"""
import csv
import io
import re
import unicodedata
from datetime import datetime
from typing import Optional


ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
SEPARATORS_TO_TRY = [";", ",", "\t", "|"]


def detect_encoding(raw: bytes) -> str:
    """Try several encodings; return the first one that decodes cleanly."""
    for enc in ENCODINGS_TO_TRY:
        try:
            text = raw.decode(enc)
            # Heuristic : if we still see replacement chars, the encoding is wrong
            if "\ufffd" in text or "�" in text:
                continue
            return enc
        except UnicodeDecodeError:
            continue
    # Last resort : latin-1 always succeeds (1 byte = 1 char)
    return "latin-1"


def detect_separator(sample_lines: list[str]) -> str:
    """Pick the separator whose occurrence count is the most consistent across the first lines."""
    if not sample_lines:
        return ","
    sep_scores: dict[str, int] = {}
    for sep in SEPARATORS_TO_TRY:
        counts = [line.count(sep) for line in sample_lines[:10] if line.strip()]
        if not counts:
            sep_scores[sep] = 0
            continue
        # Reward consistency : if every line has the same count > 0, score = count
        consistent = len(set(counts)) == 1 and counts[0] > 0
        if consistent:
            sep_scores[sep] = counts[0] * 10
        else:
            sep_scores[sep] = max(counts) if counts else 0
    return max(sep_scores, key=sep_scores.get)


def normalize_header(h: str) -> str:
    """Lowercase, strip, remove accents and replacement chars for fuzzy match.

    Example : 'R\\ufffdf\\ufffdrence externe' -> 'reference externe'
    """
    if not h:
        return ""
    h = h.replace("\ufffd", "e").replace("�", "e")
    h = unicodedata.normalize("NFD", h)
    h = "".join(c for c in h if unicodedata.category(c) != "Mn")
    return h.lower().strip()


def sniff_csv(raw: bytes) -> dict:
    """Inspect a CSV byte string and return metadata + preview.

    Returns: { encoding, separator, headers, rows (first 20), total_rows }
    """
    encoding = detect_encoding(raw)
    text = raw.decode(encoding, errors="replace")
    # Get first few non-empty lines to detect the separator
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {"encoding": encoding, "separator": ",", "headers": [],
                "rows": [], "total_rows": 0, "error": "Fichier vide"}
    sep = detect_separator(lines[:10])
    # Parse with csv module for robustness (handles quoted strings)
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows:
        return {"encoding": encoding, "separator": sep, "headers": [],
                "rows": [], "total_rows": 0, "error": "Aucune ligne"}
    headers_raw = [h.strip() for h in all_rows[0]]
    data = all_rows[1:]
    preview = data[:20]
    return {
        "encoding": encoding,
        "separator": sep,
        "headers": headers_raw,
        "headers_normalized": [normalize_header(h) for h in headers_raw],
        "rows": preview,
        "total_rows": len(data),
    }


def parse_french_number(s: str) -> float:
    """Parse '1.234,56' or '1 234,56' or '1234.56' or '1234,56' -> float.

    Returns 0.0 if input is empty / not parsable.
    """
    if s is None:
        return 0.0
    s = str(s).strip()
    if not s:
        return 0.0
    s = s.replace(" ", "").replace("\u00a0", "")
    # If both `.` and `,` present, the last one is the decimal separator
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_date(s: str) -> Optional[str]:
    """Parse a date string from many common formats. Returns ISO YYYY-MM-DD or None."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def split_optipro_code(s: str) -> tuple[str, str]:
    """Optipro uses 'CODE - LIBELLE' format (ex: 'F0471 - SRL ACE Garden').

    Returns (code, libelle). If no separator, returns (s, s).
    """
    if not s:
        return ("", "")
    s = str(s).strip()
    if " - " in s:
        parts = s.split(" - ", 1)
        return (parts[0].strip(), parts[1].strip())
    return (s, s)
