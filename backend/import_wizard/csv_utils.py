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


def split_ref_code(s: str) -> tuple[str, str]:
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


# ============================================================
# Optipro INVOICES CSV parser
# ============================================================
def parse_invoices_csv(raw: bytes) -> dict:
    """Parse Optipro 'facture' CSV export.

    Expected headers (in any order, case-insensitive, accents-tolerant) :
      Copropriete code, Copropriete nom, Date facture, Date echeance,
      Reference interne, Reference externe, Libelle, Ne pas payer, Fournisseur,
      Compte, Cle, Dossier travaux, Nature, Code TVA, Part occupant,
      Part proprietaire, Montant HT, Montant TVAC

    Returns:
      { invoices: [{
          copro_code, copro_name,
          date, due_date,
          internal_ref, external_ref,
          libelle, ne_pas_payer,
          supplier_aux_code, supplier_name,
          account_number, account_label,
          dist_key_code, dist_key_label,
          nature_code, nature_label,
          vat_code,
          part_occupant, part_proprietaire,
          montant_ht, montant_tvac, montant_tva,
        }, ...],
        count }
    """
    enc = detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
    # Sniff separator from first lines
    sample_lines = text.splitlines()[:5]
    sep = detect_separator(sample_lines)
    reader = csv.DictReader(io.StringIO(text), delimiter=sep)

    # Build a header map : normalized_header -> actual_header
    headers = reader.fieldnames or []
    nm = {normalize_header(h): h for h in headers}

    def get(row: dict, *keys: str) -> str:
        """Return first non-empty value for any normalized key in *keys."""
        for k in keys:
            actual = nm.get(k)
            if actual and row.get(actual) not in (None, ""):
                return str(row[actual]).strip()
        return ""

    invoices: list[dict] = []
    for row in reader:
        if not row:
            continue
        # Skip empty rows
        if not any((v or "").strip() for v in row.values()):
            continue

        copro_code = get(row, "copropriete code", "copro code", "code copropriete")
        copro_name = get(row, "copropriete nom", "copropriete", "nom copropriete")
        date_fact = parse_date(get(row, "date facture", "date"))
        date_ech = parse_date(get(row, "date echeance"))
        ref_int = get(row, "reference interne", "ref interne")
        ref_ext = get(row, "reference externe", "ref externe", "numero", "numero facture", "numero de facture", "n facture", "no facture", "num facture")
        libelle = get(row, "libelle", "description")
        ne_pas_payer_raw = get(row, "ne pas payer").lower()
        ne_pas_payer = ne_pas_payer_raw in ("oui", "yes", "true", "1", "x")

        sup_raw = get(row, "fournisseur", "supplier")
        sup_code, sup_name = split_ref_code(sup_raw)

        acc_raw = get(row, "compte", "account")
        acc_code, acc_label = split_ref_code(acc_raw)

        key_raw = get(row, "cle", "cle de repartition", "key")
        key_code, key_label = split_ref_code(key_raw)

        nat_raw = get(row, "nature", "nature depense")
        nat_code, nat_label = split_ref_code(nat_raw)

        vat_code = get(row, "code tva", "tva", "vat code")
        part_occ = parse_french_number(get(row, "part occupant", "occupant"))
        part_prop = parse_french_number(get(row, "part proprietaire", "proprietaire"))
        mt_ht = parse_french_number(get(row, "montant ht", "ht"))
        mt_tvac = parse_french_number(get(row, "montant tvac", "tvac", "ttc", "total"))
        # VAT amount = TVAC - HT
        mt_tva = round(mt_tvac - mt_ht, 2) if mt_tvac and mt_ht else 0.0

        # Don't import rows that are obviously not invoices (empty supplier + 0 amounts)
        if not sup_raw and mt_tvac == 0:
            continue

        invoices.append({
            "copro_code": copro_code,
            "copro_name": copro_name,
            "date": date_fact or "",
            "due_date": date_ech or "",
            "internal_ref": ref_int,
            "external_ref": ref_ext,
            "libelle": libelle,
            "ne_pas_payer": ne_pas_payer,
            "supplier_aux_code": sup_code,
            "supplier_name": sup_name,
            "account_number": acc_code,
            "account_label": acc_label,
            "dist_key_code": key_code,
            "dist_key_label": key_label,
            "nature_code": nat_code,
            "nature_label": nat_label,
            "vat_code": vat_code,
            "part_occupant": part_occ,
            "part_proprietaire": part_prop,
            "montant_ht": mt_ht,
            "montant_tvac": mt_tvac,
            "montant_tva": mt_tva,
        })

    return {"invoices": invoices, "count": len(invoices)}


# ============================================================
# Optipro JOURNALS CSV parser (Phase H - bank statements)
# ============================================================
def parse_journals_csv(raw: bytes) -> dict:
    """Parse Optipro 'journaux financiers' CSV export.

    Each transaction is split across multiple lines (double-entry accounting):
    one debit line and one credit line, both sharing the same `Num. doc`.
    We group lines by `Num. doc` to reconstruct the transactions.

    Expected headers:
      Date valeur, Num. doc, Libelle, Compte, Compte libelle, Auxiliaire,
      Identite, Code journal, Date comptabilisation, Date echeance,
      Reference interne, Reference piece, Date piece, Code lettrage,
      Date lettrage, Code TVA, Debit, Credit

    Returns:
      { transactions: [{
          date_value, num_doc, code_journal, libelle,
          bank_account, bank_account_label,
          counterparty_account, counterparty_account_label,
          amount, direction (in/out),
          ext_reference,
        }],
        raw_lines: [{...}],  # all individual debit/credit lines
        count }
    """
    enc = detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
    sample_lines = text.splitlines()[:5]
    sep = detect_separator(sample_lines)
    reader = csv.DictReader(io.StringIO(text), delimiter=sep)
    headers = reader.fieldnames or []
    nm = {normalize_header(h): h for h in headers}

    def get(row: dict, *keys: str) -> str:
        for k in keys:
            actual = nm.get(k)
            if actual and row.get(actual) not in (None, ""):
                return str(row[actual]).strip()
        return ""

    raw_lines: list[dict] = []
    # Bank account prefix (cash/bank accounts in PCMN are 550-559, 570-579)
    def is_bank_account(acc: str) -> bool:
        if not acc:
            return False
        return acc.startswith("55") or acc.startswith("57") or acc.startswith("416") or acc.startswith("417")

    for row in reader:
        if not row or not any((v or "").strip() for v in row.values()):
            continue
        line = {
            "date_value": parse_date(get(row, "date valeur", "date")) or "",
            "num_doc": get(row, "num doc", "numero document", "num. doc"),
            "libelle": get(row, "libelle", "description"),
            "account": get(row, "compte"),
            "account_label": get(row, "compte libelle", "libelle compte"),
            "auxiliary": get(row, "auxiliaire"),
            "identity": get(row, "identite"),
            "code_journal": get(row, "code journal", "journal"),
            "date_compta": parse_date(get(row, "date comptabilisation")) or "",
            "date_echeance": parse_date(get(row, "date echeance")) or "",
            "ext_reference": get(row, "reference externe", "reference piece", "ref ext"),
            "vat_code": get(row, "code tva", "tva"),
            "debit": parse_french_number(get(row, "debit")),
            "credit": parse_french_number(get(row, "credit")),
        }
        raw_lines.append(line)

    # Group by num_doc to build transactions
    by_doc: dict[str, list[dict]] = {}
    for ln in raw_lines:
        key = ln["num_doc"] or f"ROW_{len(by_doc)}"
        by_doc.setdefault(key, []).append(ln)

    transactions: list[dict] = []
    for num_doc, lines in by_doc.items():
        # Identify the bank line (account starts with 55x or 57x) and the counterparty line
        bank_lines = [ln for ln in lines if is_bank_account(ln["account"])]
        other_lines = [ln for ln in lines if not is_bank_account(ln["account"])]
        if not bank_lines or not other_lines:
            # Not a typical bank transaction (e.g. internal transfer or pure OD)
            # Still emit ONE transaction summarizing all lines
            total_d = sum(ln["debit"] for ln in lines)
            total_c = sum(ln["credit"] for ln in lines)
            amount = abs(total_d) if total_d > 0 else abs(total_c)
            # Find the identity if any
            counter_identity = next((ln["identity"] for ln in lines if ln.get("identity")), "")
            counter_aux = next((ln["auxiliary"] for ln in lines if ln.get("auxiliary")), "")
            transactions.append({
                "num_doc": num_doc,
                "date_value": lines[0]["date_value"],
                "date_compta": lines[0]["date_compta"],
                "code_journal": lines[0]["code_journal"],
                "libelle": lines[0]["libelle"],
                "bank_account": "",
                "bank_account_label": "",
                "counterparty_account": lines[0]["account"],
                "counterparty_account_label": lines[0]["account_label"],
                "counterparty_name": counter_identity,
                "counterparty_aux": counter_aux,
                "amount": amount,
                "direction": "neutral",
                "ext_reference": lines[0]["ext_reference"],
                "lines_count": len(lines),
            })
            continue
        bank = bank_lines[0]
        # The bank's debit means money IN (credit on counter-party side)
        # The bank's credit means money OUT (debit on counter-party side)
        amt = bank["debit"] if bank["debit"] > 0 else bank["credit"]
        direction = "in" if bank["debit"] > 0 else "out"
        # Counterparty : the first non-bank line (or the largest one)
        counter = other_lines[0]
        # ---- Resolve the REAL counterparty name (supplier / owner) ----
        # Optipro CSV stores it in `Identite` (e.g. "SRL ACE Garden") and
        # the auxiliary code in `Auxiliaire` (e.g. "F0471"). Without these
        # the bank statement shows a generic "Fournisseurs" label, which is
        # unreadable and makes auto-lettrage impossible. We pick the first
        # non-empty Identite among the non-bank lines.
        counter_identity = ""
        counter_aux = ""
        for ln in other_lines:
            if ln.get("identity") and not counter_identity:
                counter_identity = ln["identity"]
            if ln.get("auxiliary") and not counter_aux:
                counter_aux = ln["auxiliary"]
            if counter_identity and counter_aux:
                break
        transactions.append({
            "num_doc": num_doc,
            "date_value": bank["date_value"],
            "date_compta": bank["date_compta"],
            "code_journal": bank["code_journal"],
            "libelle": bank["libelle"] or counter["libelle"],
            "bank_account": bank["account"],
            "bank_account_label": bank["account_label"],
            "counterparty_account": counter["account"],
            "counterparty_account_label": counter["account_label"],
            "counterparty_name": counter_identity,  # real supplier/owner name
            "counterparty_aux": counter_aux,        # auxiliary code (F0XXX / C0XXX)
            "amount": round(amt, 2),
            "direction": direction,
            "ext_reference": bank["ext_reference"] or counter["ext_reference"],
            "lines_count": len(lines),
        })

    return {
        "transactions": transactions,
        "raw_lines": raw_lines,
        "count": len(transactions),
    }
