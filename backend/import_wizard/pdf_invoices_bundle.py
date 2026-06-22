"""Parser for Optipro 'Regroupement de documents' bundle PDFs.

Each bundle contains many supplier invoices concatenated, separated by blank
pages. This parser :
1. Detects invoice boundaries (group consecutive non-empty pages)
2. Extracts key metadata per invoice : supplier, invoice number, date, amount
3. Returns a structured list ready for matching against the invoices DB
"""
import io
import re
from typing import Optional, List, Dict

import pdfplumber
from pypdf import PdfReader, PdfWriter


# Common header keywords on a typical invoice (FR/EN)
INVOICE_KEYWORDS = (
    "facture", "invoice", "comptabilis",
)

# Date patterns
DATE_RE = re.compile(r"(\d{1,2})[/\.\-](\d{1,2})[/\.\-](\d{2,4})")

# Spelled-out date in French/Dutch/English : "16 mars 2025", "14 september 2025", etc.
MONTH_NAMES = {
    # French
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "septembre,": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # Dutch
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5,
    "juni": 6, "juli": 7, "augustus": 8, "september": 9,
    "oktober": 10, "november": 11, "december": 12,
    # English
    "january": 1, "february": 2, "march": 3, "may": 5,
    "june": 6, "july": 7, "august": 8,
}
SPELLED_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+("
    + "|".join(sorted(MONTH_NAMES.keys(), key=len, reverse=True))
    + r")\s+(\d{4})\b",
    re.IGNORECASE,
)

# Invoice number patterns (Optipro format like "V-250494", "FA-XXXX", "250494", "2025-...")
INVOICE_NUM_RE = re.compile(
    r"\b((?:V|FA|F|N|FACT)[\-/]?\d{4,9}|\d{4,12}|\d{4}[/-]\d{4,6})\b",
    re.IGNORECASE,
)

# Amount patterns (Belgian/French numbers : 1.234,56 or 1 234,56 or 1234.56).
# We re-allow space as thousand separator since real invoices use it (e.g. AXA, Clean & Co).
# Ambiguous cases ("1 249,58" = qty×price vs 1249.58) are handled at the caller level
# by skipping lines that look like tax breakdown tables (contain % or 4+ numbers).
AMOUNT_RE = re.compile(r"(\d{1,3}(?:[ .]\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})")

# TVA number (Belgian) : BE 0XXX.XXX.XXX or BE 1XXX.XXX.XXX
TVA_BE_RE = re.compile(r"\bBE\s*[01]\d{3}[ .]?\d{3}[ .]?\d{3}\b")


def _is_empty_page(text: str) -> bool:
    """A page is 'empty' if it has less than ~30 non-whitespace chars."""
    if not text:
        return True
    cleaned = re.sub(r"\s+", "", text)
    return len(cleaned) < 30


def _parse_amount_str(s: str) -> float:
    """Parse a number string detected by AMOUNT_RE.

    Belgian format ("1.234,56") -> dot is thousands separator, comma is decimal.
    US format ("1234.56") -> dot is decimal.
    Plain ("1234,56") -> comma is decimal.
    Returns 0.0 if the string is implausible.
    """
    s = s.strip().replace(" ", "")
    if "," in s:
        # BE / FR : remove thousand dots, then convert comma to dot
        s = s.replace(".", "").replace(",", ".")
    # else: US format, keep as-is (already valid "1234.56" -> 1234.56)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _is_plausible_amount(v: float) -> bool:
    return 0.01 <= v <= 100000.0


def _is_plausible_date(yy: str, mm: str, dd: str) -> bool:
    """Validate date components from regex match."""
    try:
        y, m, d = int(yy), int(mm), int(dd)
    except (ValueError, TypeError):
        return False
    if y < 2000 or y > 2035:
        return False
    if m < 1 or m > 12:
        return False
    if d < 1 or d > 31:
        return False
    return True


def _looks_like_tax_breakdown_row(ln: str) -> bool:
    """True if the line looks like a TVA tax breakdown row OR a qty x price row.

    Patterns covered :
      - "21% rate row" : starts with "21 " followed by amounts (TVA breakdown)
      - "qty x price" : has 2+ amounts AND a leading "X " (single small digit) before an amount
    """
    # Lines containing "%" -> almost always a TVA rate row
    if "%" in ln:
        return True
    matches = list(AMOUNT_RE.finditer(ln))
    if len(matches) < 2:
        return False
    stripped = ln.strip()
    # Pattern : line starts with 1-2 digits + space + digits, with 2+ amounts -> tax row
    if len(matches) >= 3 and re.match(r"^\d{1,2}\s+\d", stripped):
        return True
    # Pattern : the first amount has a "single small digit" prefix (e.g. "1 249,58")
    # AND the same value appears WITHOUT prefix later in the line.
    # This is the classic "quantity 1 x unit_price 249,58 = total 249,58" table row.
    if len(matches) >= 2:
        first_raw = matches[0].group(1)
        # First amount has a " " (space) thousand sep AND starts with 1-2 digits before it
        m = re.match(r"^(\d{1,2})\s+(\d{3},\d{2})$", first_raw)
        if m:
            qty_prefix, real_amount = m.groups()
            # Is the real_amount present as a separate match later in the line?
            for later in matches[1:]:
                if later.group(1).strip() == real_amount:
                    return True
    return False


def _line_looks_like_tva_or_id(ln: str) -> bool:
    """True if the line contains a VAT/BCE/IBAN/phone marker that would make
    its numbers unsuitable as invoice amount.

    Uses word boundaries to avoid false positive on "TVAC" (which contains "tva").
    """
    low = ln.lower()
    # Word-boundary checks to avoid matching inside "tvac" / "tvas" etc.
    bad_patterns = [
        r"\btva[\s:.]",       # "TVA :" "TVA."  but NOT "tvac"
        r"n[°o]\s*tva",        # "N° TVA"
        r"\bbce\b",
        r"\bvat\b",
        r"\biban\b",
        r"\bbic\b",
        r"\bsiret\b", r"\bsiren\b",
        r"\btel[\s:.]", r"\bt[ée]l[\s:.]",
        r"n[°o] entreprise", r"numero entreprise",
        r"n[°o] de r[ée]f[ée]rence",
        r"compte bancaire",
        r"\bBE\s*\d{2}\s*\d{4}",  # IBAN-like Belgian: BE70 8601 ...
    ]
    return any(re.search(p, low) for p in bad_patterns)


def _extract_invoice_metadata(pages_text: List[str], page_indices: List[int]) -> Dict:
    """From a list of consecutive page texts (one invoice block), extract metadata."""
    full_text = "\n".join(pages_text)

    # 1) Try to find invoice number (look for typical patterns near "Facture" or "Référence")
    invoice_number = ""
    for ln in full_text.split("\n"):
        ln_low = ln.lower()
        if any(kw in ln_low for kw in ("référence", "reference", "n° facture", "n°facture", "facture n", "v-")):
            m = INVOICE_NUM_RE.search(ln)
            if m:
                invoice_number = m.group(1)
                break
    if not invoice_number:
        m = INVOICE_NUM_RE.search(full_text)
        if m:
            invoice_number = m.group(1)

    # 2) Date : multi-pass cascade prioritizing reliable sources :
    #    a) Spelled-out date ("16 mars 2025") near "Facture du" / "Date" - highest priority
    #    b) Numeric date NEAR "Date facture", "Date :", "Facture du" keywords
    #    c) Numeric date with year in (current_window) - 2024..2026
    #    d) Generic plausible date as last fallback
    date_iso = ""
    date_display = ""
    lines_for_date = full_text.split("\n")

    def _try_spelled_date(text):
        m = SPELLED_DATE_RE.search(text)
        if not m:
            return None
        dd, mon, yy = m.groups()
        mm = MONTH_NAMES.get(mon.lower(), 0)
        if not mm:
            return None
        if not _is_plausible_date(yy, str(mm), dd):
            return None
        return f"{yy}-{str(mm).zfill(2)}-{dd.zfill(2)}", f"{dd.zfill(2)}/{str(mm).zfill(2)}/{yy}"

    def _try_numeric_date(text, year_window=None):
        for m in DATE_RE.finditer(text):
            dd, mm, yy = m.groups()
            if len(yy) == 2:
                yy = "20" + yy
            if not _is_plausible_date(yy, mm, dd):
                continue
            if year_window and not (year_window[0] <= int(yy) <= year_window[1]):
                continue
            # Skip if the match is preceded by other digits/letters (suggests contract number)
            start = m.start()
            if start > 0 and (text[start - 1].isdigit() or text[start - 1] in "-/"):
                continue
            return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}", f"{dd.zfill(2)}/{mm.zfill(2)}/{yy}"
        return None

    # Pass a : spelled-out date (most reliable)
    sd = _try_spelled_date(full_text)
    if sd:
        date_iso, date_display = sd

    # Pass b : numeric date near a Date-keyword
    if not date_iso:
        for i, ln in enumerate(lines_for_date):
            ln_low = ln.lower()
            if any(k in ln_low for k in ("date facture", "facture du", "date du", "date d'echeance", "date :", "datum")):
                # Check this line + next 2 lines
                window = "\n".join(lines_for_date[i:i + 3])
                # Try spelled first
                sd = _try_spelled_date(window)
                if sd:
                    date_iso, date_display = sd
                    break
                # Then numeric (restricted to recent years)
                nd = _try_numeric_date(window, year_window=(2023, 2030))
                if nd:
                    date_iso, date_display = nd
                    break

    # Pass c : any numeric date with year in (2024..2026)
    if not date_iso:
        nd = _try_numeric_date(full_text, year_window=(2024, 2026))
        if nd:
            date_iso, date_display = nd

    # Pass d : any plausible date as last fallback (with anti-junk check)
    if not date_iso:
        nd = _try_numeric_date(full_text)
        if nd:
            date_iso, date_display = nd

    # 3) Supplier name (best-effort) : first non-empty line that's not a keyword
    supplier_hint = ""
    for ln in full_text.split("\n"):
        ln_strip = ln.strip()
        ln_low = ln_strip.lower()
        if not ln_strip:
            continue
        if any(kw in ln_low for kw in INVOICE_KEYWORDS):
            continue
        if "date" in ln_low and ":" in ln_low:
            continue
        if "echéance" in ln_low or "echeance" in ln_low or "écheance" in ln_low:
            continue
        if DATE_RE.match(ln_strip):
            continue
        if INVOICE_NUM_RE.match(ln_strip) and len(ln_strip) < 20:
            continue
        if len(ln_strip) < 4 or len(ln_strip) > 60:
            continue
        # Skip lines starting with numbers
        if ln_strip[0].isdigit():
            continue
        supplier_hint = ln_strip
        break

    # 4) TVA number
    tva = ""
    m = TVA_BE_RE.search(full_text)
    if m:
        tva = m.group(0).replace(" ", "").replace(".", "")

    # 5) Total amount (look for "Total TVAC", "Montant TTC", "Total" near end)
    total_amount = 0.0
    lines = full_text.split("\n")
    lines_lower = [ln.lower() for ln in lines]
    # Keywords sorted from most specific to most generic, with priorities.
    # When a keyword matches, search the same line + next 4 lines for an amount.
    # We prefer the LARGEST plausible amount found near the keyword (not just the last
    # one on the line, because secondary numbers like VAT base may follow).
    strong_keywords = (
        "total tvac", "total ttc", "montant tvac", "montant ttc",
        "total à payer", "total a payer", "net a payer",
        "totaal / total", "totaal ttc", "totaal tvac",
        "veuillez verser",
        "total eur",
    )
    weak_keywords = ("a payer", "à payer", "totaal", "total ")

    def _find_total_near(start_i):
        """Scan lines [start_i .. start_i+4] for the LARGEST plausible amount,
        skipping TVA/BCE/IBAN lines AND tax breakdown rows."""
        best = 0.0
        for j in range(start_i, min(start_i + 5, len(lines))):
            src = lines[j]
            if _line_looks_like_tva_or_id(src):
                continue
            if _looks_like_tax_breakdown_row(src):
                continue
            for m in AMOUNT_RE.finditer(src):
                v = _parse_amount_str(m.group(1))
                if _is_plausible_amount(v) and v > best:
                    best = v
        return best

    # First pass : look for strong keywords (most reliable)
    for i, ln_lower in enumerate(lines_lower):
        if any(kw in ln_lower for kw in strong_keywords):
            v = _find_total_near(i)
            if v > 0:
                total_amount = v
                break

    # Second pass : weak keywords if no strong match
    if total_amount < 0.01:
        for i, ln_lower in enumerate(lines_lower):
            if any(kw in ln_lower for kw in weak_keywords):
                v = _find_total_near(i)
                if v > 0:
                    total_amount = v
                    break

    # Fallback : take the LARGEST plausible amount (skip lines with TVA/BCE/IBAN/phone
    # AND tax breakdown rows)
    if total_amount < 0.01:
        all_amounts = []
        for j, ln in enumerate(lines):
            if _line_looks_like_tva_or_id(ln):
                continue
            if _looks_like_tax_breakdown_row(ln):
                continue
            for m in AMOUNT_RE.finditer(ln):
                v = _parse_amount_str(m.group(1))
                if _is_plausible_amount(v):
                    all_amounts.append(v)
        if all_amounts:
            total_amount = max(all_amounts)

    return {
        "page_range": page_indices,
        "page_count": len(page_indices),
        "invoice_number": invoice_number,
        "date_iso": date_iso,
        "date_display": date_display,
        "supplier_hint": supplier_hint,
        "supplier_tva": tva,
        "total_amount": round(total_amount, 2),
        "raw_text_preview": full_text[:300],
    }


def parse_invoice_bundle(raw: bytes) -> dict:
    """Parse a PDF bundle of concatenated supplier invoices.

    Returns: {
      total_pages, invoice_count, blocks: [{page_range, ...metadata}]
    }
    """
    info = {
        "total_pages": 0,
        "invoice_count": 0,
        "blocks": [],
    }
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        info["total_pages"] = len(pdf.pages)
        # Build per-page text + empty flag
        page_texts = []
        page_is_empty = []
        for page in pdf.pages:
            t = page.extract_text() or ""
            page_texts.append(t)
            page_is_empty.append(_is_empty_page(t))

    # SMART SEGMENTATION : split into invoice blocks using signature heuristics
    # (instead of just "consecutive non-empty pages")
    blocks_pages = _split_into_invoice_blocks(page_texts)

    # Extract metadata from each block
    for pi_list in blocks_pages:
        block_texts = [page_texts[i] for i in pi_list]
        meta = _extract_invoice_metadata(block_texts, [i + 1 for i in pi_list])  # 1-based pages
        info["blocks"].append(meta)
    info["invoice_count"] = len(info["blocks"])
    return info


def extract_block_pdf(raw: bytes, page_range_1based: List[int]) -> bytes:
    """Return a new PDF containing ONLY the requested pages from the bundle.

    page_range_1based : list of 1-based page numbers (e.g. [2, 3, 4])
    """
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    for p in page_range_1based:
        if 1 <= p <= len(reader.pages):
            writer.add_page(reader.pages[p - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page_invoice_signature(page_text: str) -> dict:
    """Extract per-page identifying signals (BCE, invoice number, date, totals).
    Used to decide if a page starts a new invoice or continues the previous one."""
    if not page_text:
        return {"bce": "", "inv_num": "", "date_iso": "", "has_header": False}
    head = "\n".join(page_text.split("\n")[:25])
    bce_m = re.search(r"BE\s*0\s*\d{3}[\s.]?\d{3}[\s.]?\d{3}", page_text)
    bce = bce_m.group(0).replace(" ", "").replace(".", "") if bce_m else ""

    # Invoice number near "Facture", "Réf", "Date" — best-effort
    inv_num = ""
    for ln in head.split("\n"):
        ln_low = ln.lower()
        if any(k in ln_low for k in ("référence", "reference", "n° facture", "facture n", "v-", "n°facture")):
            m = INVOICE_NUM_RE.search(ln)
            if m:
                inv_num = m.group(1)
                break
    if not inv_num:
        # Look for a "DDMMYY" date followed by an invoice number pattern (common in Optipro tables)
        m = re.search(r"\b(\d{6,9}|\d{4}/\d{2,6})\b", head)
        if m:
            inv_num = m.group(1)

    # Date (try multiple formats)
    date_iso = ""
    for m in DATE_RE.finditer(page_text):
        dd, mm, yy = m.groups()
        if len(yy) == 2:
            yy = "20" + yy
        if _is_plausible_date(yy, mm, dd):
            date_iso = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
            break

    # Has invoice header keywords?
    head_low = head.lower()
    has_facture = "facture" in head_low or "invoice" in head_low
    has_credit_note = "note de cré" in head_low or "note credit" in head_low or "note de credit" in head_low
    has_stamp = bool(re.search(r"comptabilis[ée]\s*le", head_low))
    has_header = has_facture or has_credit_note or has_stamp

    return {
        "bce": bce, "inv_num": inv_num, "date_iso": date_iso,
        "has_header": has_header, "has_facture": has_facture,
        "has_credit_note": has_credit_note, "has_stamp": has_stamp,
    }


def _split_into_invoice_blocks(page_texts: List[str]) -> List[List[int]]:
    """Smart segmentation : returns list of (list of 0-based page indices), each = 1 invoice.

    Strategy:
      - Empty pages are separators (never included in a block)
      - Each non-empty page is normally a NEW invoice (Optipro tends to use 1 page = 1 invoice)
      - A page is treated as a CONTINUATION of the previous invoice if :
        * It has no invoice header (no "Facture", "Note de credit", "Comptabilise")
        * AND it has no new BCE different from previous page's BCE
        * AND it has no new invoice number / different date
        * OR it explicitly contains "Page X/Y" with X >= 2
    """
    blocks: List[List[int]] = []
    current: List[int] = []
    prev_sig = None

    for idx, text in enumerate(page_texts):
        if _is_empty_page(text):
            if current:
                blocks.append(current)
                current = []
            prev_sig = None
            continue

        sig = _page_invoice_signature(text)
        head = "\n".join(text.split("\n")[:5]).lower()
        is_continuation = False

        if current and prev_sig is not None:
            # Strong continuation : "Page 2/Y", "Page 3/Y" etc. at top
            if re.search(r"^\s*page\s+[2-9]\s*[/de\\]", head, re.MULTILINE):
                is_continuation = True
            elif "page 2/" in head or "page 3/" in head or "page 4/" in head or "page 5/" in head:
                is_continuation = True
            elif not sig["has_header"]:
                # No invoice header on this page
                if sig["bce"] and prev_sig["bce"] and sig["bce"] == prev_sig["bce"]:
                    # Same supplier, no new header -> continuation
                    is_continuation = True
                elif not sig["bce"] and not sig["inv_num"]:
                    # No identifying info -> continuation
                    is_continuation = True
                elif sig["inv_num"] and prev_sig["inv_num"] and sig["inv_num"] == prev_sig["inv_num"]:
                    # Same invoice number -> continuation
                    is_continuation = True
            else:
                # Page has a header (Facture/Note/Comptabilise) ->
                # Check if it's a new invoice or just a stamp on a continuation.
                # Same supplier (BCE) + same invoice number + same date -> continuation
                # Different ANY signal -> new invoice
                same_bce = sig["bce"] and prev_sig["bce"] and sig["bce"] == prev_sig["bce"]
                same_inv = sig["inv_num"] and prev_sig["inv_num"] and sig["inv_num"] == prev_sig["inv_num"]
                same_date = sig["date_iso"] and prev_sig["date_iso"] and sig["date_iso"] == prev_sig["date_iso"]
                # All 3 must match for continuation. Otherwise it's a new invoice
                # of the same supplier (very common in Optipro bundles).
                if same_bce and same_inv and same_date:
                    is_continuation = True
                elif same_bce and same_inv and not sig["date_iso"]:
                    # No date on this page but same supplier+invoice number
                    is_continuation = True

        if is_continuation:
            current.append(idx)
        else:
            if current:
                blocks.append(current)
            current = [idx]
        prev_sig = sig

    if current:
        blocks.append(current)
    return blocks


def match_invoice(extracted: Dict, candidates: List[Dict]) -> Optional[Dict]:
    """Match an extracted invoice against existing invoices in DB.

    Cascade : exact invoice number > supplier+amount > supplier+date > best amount/date guess.
    Returns the best matching candidate with `match_confidence` and `match_method`.

    NEW: when matching by invoice_number, also verify the amount is plausible
    (within 5% or 1 EUR). Otherwise downgrade to lower confidence.
    """
    if not candidates:
        return None

    extracted_amount = float(extracted.get("total_amount") or 0)

    # Pass 1 : exact invoice number match (+ amount sanity)
    inv_num = (extracted.get("invoice_number") or "").strip().lower()
    if inv_num and len(inv_num) >= 4:
        for cand in candidates:
            cand_nums = [
                str(cand.get("number") or "").lower(),
                str(cand.get("internal_reference") or "").lower(),
                str(cand.get("invoice_number") or "").lower(),
            ]
            for cn in cand_nums:
                if not cn:
                    continue
                # Strict equality OR strong endswith (at least 5 chars matching at the end)
                match = False
                if cn == inv_num:
                    match = True
                elif len(inv_num) >= 5 and (cn.endswith(inv_num) or inv_num.endswith(cn)):
                    # Only accept endswith if the matching suffix is at least 5 chars
                    common_len = min(len(cn), len(inv_num))
                    if common_len >= 5:
                        match = True
                if match:
                    cand_amount = float(cand.get("total_amount") or 0)
                    # Amount sanity check
                    if extracted_amount > 0 and cand_amount > 0:
                        amt_diff = abs(cand_amount - extracted_amount)
                        if amt_diff < 0.5 or amt_diff / max(cand_amount, 0.01) < 0.05:
                            return {**cand, "match_confidence": 0.95, "match_method": "invoice_number"}
                        # invoice number matches but amount differs by > 5% -> downgrade
                        return {**cand, "match_confidence": 0.55, "match_method": "invoice_number_amount_mismatch"}
                    # No extracted amount -> still accept by invoice_number
                    return {**cand, "match_confidence": 0.85, "match_method": "invoice_number"}

    # Pass 2 : supplier name + total amount (within 1 cent)
    supplier_hint = (extracted.get("supplier_hint") or "").lower().strip()
    amount = extracted_amount
    if supplier_hint and amount > 0:
        best = None
        for cand in candidates:
            sup_name = (cand.get("supplier_name") or "").lower()
            cand_amount = float(cand.get("total_amount") or 0)
            if not sup_name or cand_amount < 0.01:
                continue
            # supplier name fuzzy (any word match)
            sup_words = {w for w in supplier_hint.split() if len(w) > 3}
            cand_words = {w for w in sup_name.split() if len(w) > 3}
            overlap = len(sup_words & cand_words)
            if abs(cand_amount - amount) < 0.5 and overlap >= 1:
                return {**cand, "match_confidence": 0.85, "match_method": "supplier_amount"}
            if abs(cand_amount - amount) < 0.01 and not best:
                best = cand
        if best:
            return {**best, "match_confidence": 0.65, "match_method": "amount_only"}

    # Pass 3 : supplier + date (within 7 days)
    date_iso = extracted.get("date_iso", "")
    if supplier_hint and date_iso:
        from datetime import date as _date
        try:
            d_ext = _date.fromisoformat(date_iso)
        except (ValueError, TypeError):
            d_ext = None
        if d_ext:
            for cand in candidates:
                sup_name = (cand.get("supplier_name") or "").lower()
                cand_date_iso = cand.get("date", "")
                if not sup_name or not cand_date_iso:
                    continue
                try:
                    d_cand = _date.fromisoformat(cand_date_iso)
                except (ValueError, TypeError):
                    continue
                if abs((d_cand - d_ext).days) <= 7:
                    sup_words = {w for w in supplier_hint.split() if len(w) > 3}
                    cand_words = {w for w in sup_name.split() if len(w) > 3}
                    if sup_words & cand_words:
                        return {**cand, "match_confidence": 0.60, "match_method": "supplier_date"}

    # Pass 4 : amount alone fallback
    if amount > 0:
        for cand in candidates:
            cand_amount = float(cand.get("total_amount") or 0)
            if abs(cand_amount - amount) < 0.01:
                return {**cand, "match_confidence": 0.50, "match_method": "amount_only_fallback"}

    return None
