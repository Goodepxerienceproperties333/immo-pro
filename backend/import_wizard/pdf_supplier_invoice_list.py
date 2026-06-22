"""Parser for Optipro 'Factures fournisseurs' list PDF.

This PDF is a tabular listing of all supplier invoices for an ACP, with one
line per invoice in the format :
    DD/MM/YYYY - F<NNNN> <Supplier> - (ref. interne) <NNNN> - (ref. externe) <X> - <description> <HT> <TVAC>
followed by 1+ allocation lines starting with the account number.

Returns a list of dict with all the structured fields, useful to compare with
existing DB records and identify duplicates / missing / mismatches.
"""
import io
import re
from typing import List, Dict

import pdfplumber


# Header line of an invoice : "DD/MM/YYYY - F<CODE> <SUPPLIER> - (ref. interne) <N> - (ref. externe) <X> - <desc> <HT> <TVAC>"
# The external ref may contain SPACES (e.g. "313 2025 062 017", "117 022 047 421").
# Strategy : capture amounts at end, then back-parse the prefix.
INVOICE_HEADER_PREFIX_RE = re.compile(
    r"^\s*(\d{2}/\d{2}/\d{4})\s*-\s*"             # 1: date
    r"(F\d{3,5})\s+"                                # 2: supplier code
    r"(.+?)\s*-\s*"                                 # 3: supplier name
    r"\(\s*ref\.?\s*interne\s*\)\s*(\d{3,6})\s*-\s*"   # 4: internal ref
    r"\(\s*ref\.?\s*externe\s*\)\s*(.+?)\s*-\s*"      # 5: external ref (NON-GREEDY, may contain spaces)
    r"(.+?)\s+"                                       # 6: description
    r"(-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s+"          # 7: HT amount
    r"(-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s*$"         # 8: TVAC amount
)

# Allocation line : "<account_number> - <desc> ... <amount> <amount>"
ALLOC_LINE_RE = re.compile(
    r"^\s*(\d{3,5})\s*-\s*(.+?)\s+"
    r"(-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s+(-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s*$"
)


def _parse_amount(s: str) -> float:
    """Convert "1 234,56" or "-788,11" to float."""
    s = s.strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _iso_date(s: str) -> str:
    """DD/MM/YYYY -> YYYY-MM-DD."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s.strip())
    if not m:
        return ""
    dd, mm, yy = m.groups()
    return f"{yy}-{mm}-{dd}"


def _looks_like_invoice_ref(s: str) -> bool:
    """True if `s` is plausibly an invoice reference (no natural language).
    Rejects strings containing French words or too long."""
    s = s.strip()
    if not s or len(s) > 35:
        return False
    # Reject if it contains common French descriptive words
    low = s.lower()
    bad_words = (
        " le ", " la ", " les ", " de ", " du ", " des ", " avec ", " pour ",
        " et ", " sur ", " sous ", " sans ", " dans ", " par ", " une ", " un ",
        " note", " ouverture", "régularisation", "regularisation", "entretien",
        "ouverture", "remplacement", "réparation", "reparation", "controle",
        "contrôle", "nettoyage", "fourniture", "honoraire", "mission", "contrat",
        "dépannage", "depannage", "expertise", "plateforme", "période", "periode",
        "arriérés", "arrieres", "tailles", "réglage", "reglage", "vitres", "ferme",
        "ouverture", "n6", "ag insurance", "expertise",
    )
    if any(b in (" " + low + " ") for b in bad_words):
        return False
    # Accept if it's alphanumeric with allowed punctuation, optionally with spaces
    # between blocks of digits/letters
    return bool(re.match(r"^[\w/.\-]+(?:\s+[\w/.\-]+)*$", s))


def _try_parse_invoice_header(ln: str) -> dict | None:
    """Try to parse an invoice header line."""
    # First : extract HT and TVAC at end + prefix
    tail_re = re.compile(
        r"^(?P<prefix>.+?)\s+"
        r"(?P<ht>-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s+"
        r"(?P<tvac>-?\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s*$"
    )
    mt = tail_re.match(ln)
    if not mt:
        return None
    prefix = mt.group("prefix")
    ht = _parse_amount(mt.group("ht"))
    tvac = _parse_amount(mt.group("tvac"))

    # Now parse the prefix
    prefix_re = re.compile(
        r"^\s*(\d{2}/\d{2}/\d{4})\s*-\s*"
        r"(F\d{3,5})\s+"
        r"(.+?)\s*-\s*"
        r"\(\s*ref\.?\s*interne\s*\)\s*(\d{3,6})\s*-\s*"
        r"\(\s*ref\.?\s*externe\s*\)\s*(.+?)$"
    )
    mp = prefix_re.match(prefix)
    if not mp:
        return None
    date_str, sup_code, sup_name, int_ref, ext_and_desc = mp.groups()

    # Split ext_ref / description :
    # The ext_ref may contain hyphens (e.g. "V-250057", "25-219").
    # Strategy : try each " - " split from LEFT to RIGHT.
    # First try: LONGEST left part that still looks like a valid ref.
    splits = list(re.finditer(r"\s+-\s+", ext_and_desc))
    ext_ref = ext_and_desc.strip()
    description = ""
    best_split_idx = -1
    for idx, split in enumerate(splits):
        left = ext_and_desc[: split.start()].strip()
        right = ext_and_desc[split.end():].strip()
        if not right:
            continue
        if _looks_like_invoice_ref(left):
            best_split_idx = idx
            # Keep going - prefer the LONGER left side that still passes
        else:
            # Once it fails, earlier matches are best
            break
    if best_split_idx >= 0:
        split = splits[best_split_idx]
        ext_ref = ext_and_desc[: split.start()].strip()
        description = ext_and_desc[split.end():].strip()
    elif splits:
        # No left part passed the test: take the FIRST split (shortest ext_ref)
        split = splits[0]
        ext_ref = ext_and_desc[: split.start()].strip()
        description = ext_and_desc[split.end():].strip()

    return {
        "date": _iso_date(date_str),
        "date_display": date_str,
        "supplier_code": sup_code,
        "supplier_name": sup_name.strip(),
        "internal_ref": int_ref,
        "external_ref": _cleanup_ref(ext_ref),
        "description": description,
        "ht_amount": ht,
        "tvac_amount": tvac,
        "allocations": [],
    }


def _cleanup_ref(s: str) -> str:
    """Trim trailing period/date fragments from a parsed ref.

    Examples:
      "2504033129 - 01/2026" -> "2504033129"
      "117 022 047 421 - 07" -> "117 022 047 421"
      "20250101 - 01/01/202" -> "20250101"
      "V-250057 - AGE 2024" -> "V-250057"
      "202502 - 28/02/2025" -> "202502"
    """
    s = s.strip()
    if " - " not in s:
        return s
    # Recompose : keep the first part if remainder looks like a date/period
    parts = s.split(" - ")
    head = parts[0].strip()
    tail = " - ".join(parts[1:]).strip()
    # Tail is a trailing date/period if : pure digits/slashes/short text
    if re.match(r"^[\d/.\-\s]+$", tail):
        return head
    if len(tail) < 12 and re.match(r"^[\w\s/]+$", tail):
        return head
    return s


def parse_supplier_invoice_list(raw: bytes) -> List[Dict]:
    """Parse the PDF list of supplier invoices and return list of structured dicts.

    Each dict has:
      date (ISO), supplier_code, supplier_name, internal_ref, external_ref,
      description, ht_amount, tvac_amount, allocations (list of dicts)
    """
    invoices: List[Dict] = []
    current: Dict | None = None

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        all_lines: List[str] = []
        for page in pdf.pages:
            txt = page.extract_text() or ""
            for ln in txt.split("\n"):
                ln = ln.rstrip()
                if ln:
                    all_lines.append(ln)

    for ln in all_lines:
        # Try invoice header first
        parsed = _try_parse_invoice_header(ln)
        if parsed:
            if current:
                invoices.append(current)
            current = parsed
            continue

        # Try allocation line (within current invoice)
        if current is not None:
            am = ALLOC_LINE_RE.match(ln)
            if am:
                acc, desc, ht, tvac = am.groups()
                current["allocations"].append({
                    "account_number": acc,
                    "description": desc.strip(),
                    "ht_amount": _parse_amount(ht),
                    "tvac_amount": _parse_amount(tvac),
                })

    if current:
        invoices.append(current)

    return invoices


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/factures_v2.pdf"
    with open(path, "rb") as f:
        raw = f.read()
    invoices = parse_supplier_invoice_list(raw)
    print(f"Parsed {len(invoices)} invoices")
    for i, inv in enumerate(invoices):
        print(f"[{i+1:2d}] {inv['date']} | {inv['supplier_name'][:25]:<26}| n={inv['external_ref'][:20]:<20}| {inv['tvac_amount']:>9.2f}E | allocs={len(inv['allocations'])}")
