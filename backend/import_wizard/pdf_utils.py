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


# ============================================================
# BUDGET PDF parser
# ============================================================
def parse_budget_pdf(raw: bytes) -> dict:
    """Parse 'Budget' PDF from Optipro.

    Structure observed : sections grouped by distribution key code (e.g. "0001 -
    Charges communes"). Each section lists PCMN accounts with budgeted amounts.

    Returns: { sections: [{ key_code, key_label, lines: [{account, libelle, amount}] }],
               total_global, count }
    """
    info = extract_pdf(raw)
    sections: list[dict] = []
    current_section: dict = None
    total_global = 0.0

    # Strategy : iterate the full text line by line and use regex to detect
    # - section headers : starts with 4-digit code, e.g. "0001 - Charges communes"
    # - data lines : starts with a PCMN account number (3-7 digits) + libelle + amount
    import re
    full_text = info.get("full_text", "")
    for raw_line in full_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        # Section header : "0001 - Libelle"
        sec_match = re.match(r"^(\d{4})\s*[-–]\s*(.+?)(?:\s+\d[\d\s.,]*)?$", line)
        # We accept the section header only if it doesn't look like a data line
        if sec_match and not re.search(r"\d+[.,]\d{2}\s*$", line):
            code = sec_match.group(1)
            label = sec_match.group(2).strip()
            current_section = {"key_code": code, "key_label": label, "lines": [], "subtotal": 0.0}
            sections.append(current_section)
            continue
        # Data line : account + libelle + amount at the end
        # Account is 3-7 digits, can be 6 or 7 digits (612590)
        # Amount ends with "x.xx" or "-x.xx"
        dl_match = re.match(r"^(\d{3,7})\s+(.+?)\s+(-?[\d\s]+[.,]\d{2})\s*$", line)
        if dl_match and current_section is not None:
            account = dl_match.group(1)
            libelle = dl_match.group(2).strip()
            amount = _to_float(dl_match.group(3).replace(" ", "").replace(",", "."))
            current_section["lines"].append({
                "account": account,
                "libelle": libelle,
                "amount": amount,
            })
            current_section["subtotal"] += amount
            total_global += amount
            continue
        # "Total" line at the end : extract the total_global
        total_match = re.search(r"total[^\d]*(-?[\d\s]+[.,]\d{2})", line.lower())
        if total_match and "section" not in line.lower():
            tg = _to_float(total_match.group(1).replace(" ", "").replace(",", "."))
            if tg > total_global:
                total_global = tg
    return {
        "sections": sections,
        "total_global": round(total_global, 2),
        "count": sum(len(s["lines"]) for s in sections),
    }


# ============================================================
# DISTRIBUTION KEYS PDF parser
# ============================================================
def parse_distribution_keys_pdf(raw: bytes) -> dict:
    """Parse 'Cle de repartition' PDF from Optipro.

    Each key has : code + libelle + lots with quotities + total.
    Returns: { keys: [{code, name, type, lines: [{lot_label, lot_code, quotity}], total_quotities}] }
    """
    info = extract_pdf(raw)
    keys: list[dict] = []
    seen_codes: set[str] = set()

    import re
    for page in info["pages"]:
        # Try to extract structured tables first
        for table in page["tables"]:
            if not table or len(table) < 1:
                continue
            # Heuristic : if the table has a "TOTAL QUOTITES" column, parse it
            header_norm = [(c or "").lower().strip().replace("\n", " ") for c in table[0]]
            has_total_qt = any("total" in h and ("quotit" in h or "qt" in h) for h in header_norm)
            has_libelle = any("libelle" in h or "libellé" in h for h in header_norm)
            if not (has_total_qt and has_libelle):
                continue
            # Locate columns
            col_idx: dict[str, int] = {}
            for i, h in enumerate(header_norm):
                if ("libelle" in h or "libellé" in h) and "copro" not in h:
                    col_idx.setdefault("libelle", i)
                elif "copropri" in h:
                    col_idx.setdefault("owner", i)
                elif "total" in h and ("quotit" in h or "qt" in h):
                    col_idx.setdefault("qt", i)
                elif "lot" in h:
                    col_idx.setdefault("lot", i)
            # Iterate data rows; the key code/label is somewhere in the row
            for row in table[1:]:
                if not row or len(row) < 3:
                    continue
                # Try to split each cell by `\n` similar to natures parser
                def get_cell(name):
                    if name not in col_idx:
                        return []
                    idx = col_idx[name]
                    if idx >= len(row):
                        return []
                    raw_cell = row[idx] or ""
                    parts = [p.strip() for p in raw_cell.split("\n") if p.strip()]
                    return parts
                libelles = get_cell("libelle")
                owners = get_cell("owner") if "owner" in col_idx else []
                lots = get_cell("lot") if "lot" in col_idx else []
                qts = get_cell("qt")
                # The first libelle is typically the KEY name (e.g. "0001 - Charges communes")
                if not libelles:
                    continue
                first_libelle = libelles[0]
                code_match = re.match(r"^(\d{3,4})\s*[-–]\s*(.+)$", first_libelle)
                # If no key code at the top of the row -> skip (not a new key, just a continuation)
                if not code_match:
                    continue
                key_code = code_match.group(1)
                key_name = code_match.group(2).strip()
                if key_code in seen_codes:
                    continue
                seen_codes.add(key_code)
                # Lines : remaining libelles paired with quotities and owners/lots
                lines = []
                total_qt = 0.0
                for i in range(1, max(len(libelles), len(qts))):
                    if i >= len(qts):
                        continue
                    qt_str = qts[i]
                    qt = _to_float(qt_str)
                    lot_label = libelles[i] if i < len(libelles) else ""
                    owner_label = owners[i] if i < len(owners) else ""
                    lot_code = lots[i] if i < len(lots) else ""
                    if qt > 0 or lot_label:
                        lines.append({
                            "lot_label": lot_label,
                            "lot_code": lot_code,
                            "owner_label": owner_label,
                            "quotity": qt,
                        })
                        total_qt += qt
                # Last quotity in the row could be the total (e.g. 168.00 vs sum of 39+40+49+40)
                if qts:
                    last_qt = _to_float(qts[0])
                    if abs(last_qt - total_qt) > 0.01 and last_qt > 0:
                        # Use the explicit total if available
                        explicit_total = last_qt
                    else:
                        explicit_total = total_qt
                else:
                    explicit_total = total_qt
                keys.append({
                    "code": key_code,
                    "name": key_name,
                    "type": "tantiemes",
                    "lines": lines,
                    "total_quotities": round(explicit_total, 6),
                })
    # Fallback : if no key parsed from tables, attempt text-based extraction
    if not keys:
        text = info.get("full_text", "")
        key_blocks = re.split(r"(?=^\d{3,4}\s*[-–]\s*[A-Z])", text, flags=re.MULTILINE)
        for block in key_blocks:
            kmatch = re.match(r"^(\d{3,4})\s*[-–]\s*(.+?)$", block.strip().splitlines()[0] if block.strip() else "")
            if not kmatch:
                continue
            keys.append({
                "code": kmatch.group(1),
                "name": kmatch.group(2).strip(),
                "type": "tantiemes",
                "lines": [],
                "total_quotities": 0.0,
            })
    return {"keys": keys, "count": len(keys)}


def _to_float(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0
