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

# Invoice number patterns (Optipro format like "V-250494", "FA-XXXX", "250494", "2025-...")
INVOICE_NUM_RE = re.compile(
    r"\b((?:V|FA|F|N|FACT)[\-/]?\d{4,9}|\d{4,12}|\d{4}[/-]\d{4,6})\b",
    re.IGNORECASE,
)

# Amount patterns (Belgian/French numbers : 1.234,56 or 1 234,56 or 1234.56)
AMOUNT_RE = re.compile(r"(\d{1,3}(?:[ .]\d{3})*,\d{2}|\d+,\d{2}|\d+\.\d{2})")

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


def _line_looks_like_tva_or_id(ln: str) -> bool:
    """True if the line contains a VAT/BCE/IBAN/phone marker that would make
    its numbers unsuitable as invoice amount."""
    low = ln.lower()
    return any(k in low for k in (
        "tva", "bce", "vat", "iban", "bic", "siret", "siren", "tel", "tél",
        "n° entreprise", "numero entreprise", "n° de référence", "compte bancaire",
    ))


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

    # 2) Date : look for "Date :" first, else first plausible date
    date_iso = ""
    date_display = ""
    for ln in full_text.split("\n"):
        if "date" in ln.lower() and ":" in ln:
            m = DATE_RE.search(ln)
            if m:
                dd, mm, yy = m.groups()
                if len(yy) == 2:
                    yy = "20" + yy
                if _is_plausible_date(yy, mm, dd):
                    date_iso = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
                    date_display = f"{dd.zfill(2)}/{mm.zfill(2)}/{yy}"
                    break
    if not date_iso:
        # Try ALL dates found in the doc and pick the first plausible one
        for m in DATE_RE.finditer(full_text):
            dd, mm, yy = m.groups()
            if len(yy) == 2:
                yy = "20" + yy
            if _is_plausible_date(yy, mm, dd):
                date_iso = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
                date_display = f"{dd.zfill(2)}/{mm.zfill(2)}/{yy}"
                break

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
    for i, ln_lower in enumerate(lines_lower):
        if any(kw in ln_lower for kw in (
            "total tvac", "total ttc", "montant tvac", "total tvac €", "total tvac e",
            "total ttc €", "total ttc e", "total à payer", "a payer", "net a payer",
            "total a payer", "montant ttc",
        )):
            # find amount in this line or the next 2 lines, skipping TVA/BCE lines
            for j in range(i, min(i + 3, len(lines))):
                src = lines[j]
                if _line_looks_like_tva_or_id(src):
                    continue
                ms = AMOUNT_RE.findall(src)
                if ms:
                    # Take the LAST plausible number on the line (rightmost = total)
                    for cand in reversed(ms):
                        v = _parse_amount_str(cand)
                        if _is_plausible_amount(v):
                            total_amount = v
                            break
                    if total_amount > 0:
                        break
            if total_amount > 0:
                break

    # Fallback : take the LARGEST plausible amount (skip lines with TVA/BCE/IBAN/phone)
    if total_amount < 0.01:
        all_amounts = []
        for j, ln in enumerate(lines):
            if _line_looks_like_tva_or_id(ln):
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

    # Group consecutive non-empty pages into "blocks" (each block = 1 invoice)
    blocks_pages: List[List[int]] = []
    current: List[int] = []
    for idx, empty in enumerate(page_is_empty):
        if empty:
            if current:
                blocks_pages.append(current)
                current = []
        else:
            current.append(idx)
    if current:
        blocks_pages.append(current)

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


def match_invoice(extracted: Dict, candidates: List[Dict]) -> Optional[Dict]:
    """Match an extracted invoice against existing invoices in DB.

    Cascade : exact invoice number > supplier+amount > supplier+date > best amount/date guess.
    Returns the best matching candidate with `match_confidence` and `match_method`.
    """
    if not candidates:
        return None

    # Pass 1 : exact invoice number match
    inv_num = (extracted.get("invoice_number") or "").strip().lower()
    if inv_num and len(inv_num) >= 4:
        for cand in candidates:
            cand_nums = [
                str(cand.get("number") or "").lower(),
                str(cand.get("internal_reference") or "").lower(),
                str(cand.get("invoice_number") or "").lower(),
            ]
            for cn in cand_nums:
                if cn and (cn == inv_num or cn.endswith(inv_num) or inv_num.endswith(cn)):
                    return {**cand, "match_confidence": 0.95, "match_method": "invoice_number"}

    # Pass 2 : supplier name + total amount (within 1 cent)
    supplier_hint = (extracted.get("supplier_hint") or "").lower().strip()
    amount = float(extracted.get("total_amount") or 0)
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
