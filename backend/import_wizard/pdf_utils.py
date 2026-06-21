"""PDF utilities for Optipro/Sogis exports (Bilan, Budget, Natures, Cles).

Uses pdfplumber for text + table extraction.
"""
import io
from typing import Optional
import pdfplumber


def extract_pdf(raw: bytes) -> dict:
    """Extract text and tables from a PDF file.

    Returns: {
      pages: [{
        page_num: int,
        text: str,
        tables: [ [[cell, ...], ...], ... ]  # raw tables as 2D arrays
      }, ...],
      total_pages: int,
      full_text: str,
    }
    """
    pages_out = []
    full_text_parts = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages_out.append({
                "page_num": i + 1,
                "text": text,
                "tables": tables,
            })
            full_text_parts.append(text)
    return {
        "pages": pages_out,
        "total_pages": len(pages_out),
        "full_text": "\n".join(full_text_parts),
    }


def parse_natures_pdf(raw: bytes) -> dict:
    """Parse 'Liste des natures de depense' PDF from Optipro.

    pdfplumber may extract the whole table as a single row where each cell
    contains newline-separated values. We split each cell by `\\n` and zip
    columns together.

    Expected columns: CODE | LIBELLE | COMPTE | CODE TVA | PART OCCUPANT | PART PROPRIETAIRE
    Returns: { natures: [{code, libelle, account_number, vat_code, part_occupant, part_proprietaire}], count }
    """
    info = extract_pdf(raw)
    natures: list[dict] = []
    seen_codes: set[str] = set()
    for page in info["pages"]:
        for table in page["tables"]:
            if not table or len(table) < 1:
                continue
            header_row = table[0]
            header_norm = [(c or "").lower().strip().replace("\n", " ") for c in header_row]
            col_idx: dict[str, int] = {}
            for i, h in enumerate(header_norm):
                if "code" in h and "tva" not in h:
                    col_idx.setdefault("code", i)
                elif "libelle" in h or "libellé" in h:
                    col_idx.setdefault("libelle", i)
                elif "compte" in h:
                    col_idx.setdefault("account", i)
                elif "tva" in h:
                    col_idx.setdefault("vat", i)
                elif "occupant" in h:
                    col_idx.setdefault("occ", i)
                elif "propri" in h:
                    col_idx.setdefault("own", i)
            if "code" not in col_idx or "libelle" not in col_idx or "account" not in col_idx:
                continue
            # For each data row, each cell can hold multiple lines. Split + zip.
            for row in table[1:]:
                if not row:
                    continue
                # Split each cell by newline
                split_cells: list[list[str]] = []
                max_len = 0
                for c in row:
                    parts = [p.strip() for p in (c or "").split("\n")]
                    parts = [p for p in parts if p]
                    split_cells.append(parts)
                    if len(parts) > max_len:
                        max_len = len(parts)
                if max_len == 0:
                    continue
                # Compact each column to align with codes (codes drive the row count)
                code_cell = split_cells[col_idx["code"]] if col_idx["code"] < len(split_cells) else []
                if not code_cell:
                    continue
                code_count = len(code_cell)
                libelle_cell = _expand_multiline(split_cells[col_idx["libelle"]], code_count) if col_idx["libelle"] < len(split_cells) else []
                account_cell = split_cells[col_idx["account"]] if col_idx["account"] < len(split_cells) else []
                vat_cell = split_cells[col_idx["vat"]] if "vat" in col_idx and col_idx["vat"] < len(split_cells) else []
                occ_cell = split_cells[col_idx["occ"]] if "occ" in col_idx and col_idx["occ"] < len(split_cells) else []
                own_cell = split_cells[col_idx["own"]] if "own" in col_idx and col_idx["own"] < len(split_cells) else []
                for i, code in enumerate(code_cell):
                    code = code.strip()
                    if not code or code in seen_codes:
                        continue
                    seen_codes.add(code)
                    libelle = libelle_cell[i] if i < len(libelle_cell) else ""
                    account = account_cell[i] if i < len(account_cell) else ""
                    vat_raw = vat_cell[i] if i < len(vat_cell) else ""
                    occ_raw = occ_cell[i] if i < len(occ_cell) else ""
                    own_raw = own_cell[i] if i < len(own_cell) else ""
                    # Clean VAT : keep only the code (A1, A2, A4, etc.) without parentheses
                    vat_code = (vat_raw.split("(")[0] or "").strip()
                    if vat_code in ("-", ""):
                        vat_code = ""
                    # Percentages may be wrapped in `(...)` — extract numbers
                    natures.append({
                        "code": code,
                        "libelle": libelle.strip(),
                        "account_number": account.strip(),
                        "vat_code": vat_code,
                        "part_occupant": _extract_pct(occ_raw),
                        "part_proprietaire": _extract_pct(own_raw),
                    })
    return {"natures": natures, "count": len(natures)}


def _expand_multiline(parts: list[str], target_count: int) -> list[str]:
    """Merge wrapped lines back together. If pdfplumber split a long libelle into 2
    rows, we need to merge them so that len(libelles) == len(codes).
    Strategy : naive bottom-up — if we have more libelle lines than codes, merge
    consecutive lines until lengths match.
    """
    if len(parts) <= target_count or target_count == 0:
        return parts + [""] * (target_count - len(parts))
    # Need to merge : compute how many merges needed
    excess = len(parts) - target_count
    merged = list(parts)
    # Heuristic : merge lines that are continuations (don't start with a capital + space, or start lowercase)
    while excess > 0 and len(merged) > target_count:
        # Find best merge candidate (line that doesn't look like a start of a label)
        for i in range(1, len(merged)):
            if merged[i] and (merged[i][0].islower() or merged[i].startswith("installations")
                              or merged[i].startswith("selon") or merged[i].startswith("(")
                              or merged[i].startswith("immediat") or merged[i].startswith("imm")):
                merged[i - 1] = merged[i - 1] + " " + merged[i]
                merged.pop(i)
                excess -= 1
                break
        else:
            # No clear continuation : merge the first two anyway
            merged[0] = merged[0] + " " + merged[1]
            merged.pop(1)
            excess -= 1
    return merged


def _extract_pct(s: str) -> float:
    """Extract a percentage from a string like '(0.00%)' or '100.00%' or '-'."""
    if not s:
        return 0.0
    import re
    m = re.search(r"([\d.,]+)\s*%", s)
    if not m:
        return 0.0
    return _to_float(m.group(1))


def _to_float(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0
