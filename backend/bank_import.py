"""Iter90l - Import PDF/CSV d'extraits de compte -> creation auto de bank_statements + bank_transactions en status='draft'.

Strategie :
- CSV : parser generique smart (detection separateur + heuristique colonnes)
        avec fallback IA Gemini si les colonnes ne matchent aucun format connu.
- PDF : IA Gemini Vision (LlmChat avec FileContentWithMimeType).

Le module retourne une structure normalisee que la route utilise pour
persister les documents.

Format retourne :
{
    "extraction_method": "csv_smart" | "llm_text" | "csv_llm_fallback",
    "account_number": "BE12...",  # peut etre vide
    "period_from": "YYYY-MM-DD" | "",
    "period_to": "YYYY-MM-DD" | "",
    "opening_balance": float,
    "closing_balance": float,
    "transactions": [
        {
            "date": "YYYY-MM-DD",
            "amount": float,   # signe : + credit, - debit
            "transaction_type": "credit"|"debit",
            "counterparty_name": str,
            "counterparty_account": str,
            "communication": str,
        }
    ],
    "warnings": [str],
}
"""
import os
import csv
import io
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone, date
from typing import Dict, List, Any, Tuple, Optional


# ---------- Utilities ----------


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _norm_header(s: str) -> str:
    s = _strip_accents((s or "").strip().lower())
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _parse_amount(v) -> Optional[float]:
    """Parse un montant en euros. Supporte '1.234,56', '1,234.56', '-25,00',
    '25,00-', '(25,00)' et parentheses = negatif."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = False
    if s.endswith("-"):
        neg = True
        s = s[:-1].strip()
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()
    # Detecter separateur decimal : si contient "," ET ".", le dernier
    # est le decimal.
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    # Supprimer devises courantes
    s = re.sub(r"[€$£\s]", "", s)
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


def _parse_date(s) -> Optional[str]:
    """Parse une date en YYYY-MM-DD depuis divers formats courants belges."""
    if not s:
        return None
    if isinstance(s, (datetime, date)):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Format 20260215
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


# ---------- CSV smart parser ----------


# Alias multi-banques pour les colonnes (Belfius, BNPP Fortis, KBC, ING, etc.).
# Les headers sont normalises (lowercase + accents stripped + _).
_ALIASES = {
    "date": ["date", "date_valeur", "date_de_valeur", "date_de_l_operation",
             "date_operation", "date_execution", "boekingsdatum", "valutadatum",
             "date_comptable", "verrichtingsdatum"],
    "amount": ["montant", "amount", "bedrag", "montant_eur", "montant_de_la_transaction",
               "bedrag_van_de_verrichting"],
    "debit": ["debit", "debet", "montant_debit", "sortie", "uitgave"],
    "credit": ["credit", "kredit", "montant_credit", "entree", "recette", "inkomst"],
    "communication": ["communication", "communication_libre", "communication_structuree",
                      "mededeling", "libelle", "libelle_operation", "details", "reference",
                      "message", "reference_de_l_operation"],
    "counterparty": ["contrepartie", "nom_contrepartie", "beneficiaire", "donneur_d_ordre",
                     "tegenpartij", "counterparty", "compte_contrepartie_nom",
                     "counterparty_name", "third_party"],
    "counterparty_account": ["iban_contrepartie", "compte_contrepartie",
                             "counterparty_account", "iban_tegenpartij", "tegenrekening"],
    "type": ["type", "sens", "signe", "type_operation", "aard"],
}


def _match_col(col_norm: str, kind: str) -> bool:
    return col_norm in _ALIASES.get(kind, [])


def _find_column(headers_norm: List[str], kind: str) -> Optional[int]:
    for i, h in enumerate(headers_norm):
        if _match_col(h, kind):
            return i
    return None


def _detect_csv_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=[";", ",", "\t", "|"])
    except Exception:
        class Dialect(csv.Dialect):
            delimiter = ";"
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return Dialect


def _find_header_row(rows: List[List[str]]) -> int:
    """Trouve la ligne d'header en cherchant les mots-cles standards.
    Retourne l'index (0-based) ou -1 si pas trouve."""
    scan_limit = min(20, len(rows))
    for i in range(scan_limit):
        row_norm = [_norm_header(c) for c in rows[i]]
        score = 0
        for kind in ("date", "amount", "debit", "credit", "communication",
                     "counterparty"):
            for h in row_norm:
                if _match_col(h, kind):
                    score += 1
                    break
        if score >= 2:  # au moins 2 colonnes reconnues
            return i
    return -1


def parse_csv_smart(content: bytes, filename: str = "") -> Dict[str, Any]:
    """Tente de parser un CSV bancaire generique. Renvoie soit une structure
    complete soit `{"extraction_method": "csv_unrecognized", ...}` avec
    warnings pour fallback IA."""
    # Decoder
    text = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return {"extraction_method": "csv_unrecognized",
                "warnings": ["Impossible de decoder le CSV"],
                "transactions": []}
    sample = text[:2048]
    dialect = _detect_csv_dialect(sample)
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    all_rows = [r for r in reader if any(c.strip() for c in r)]
    if not all_rows:
        return {"extraction_method": "csv_unrecognized",
                "warnings": ["CSV vide"], "transactions": []}
    header_idx = _find_header_row(all_rows)
    if header_idx < 0:
        return {"extraction_method": "csv_unrecognized",
                "warnings": ["En-tetes non reconnues"], "transactions": []}
    headers_norm = [_norm_header(c) for c in all_rows[header_idx]]
    data_rows = all_rows[header_idx + 1:]

    col_date = _find_column(headers_norm, "date")
    col_amount = _find_column(headers_norm, "amount")
    col_debit = _find_column(headers_norm, "debit")
    col_credit = _find_column(headers_norm, "credit")
    col_comm = _find_column(headers_norm, "communication")
    col_cp = _find_column(headers_norm, "counterparty")
    col_cp_acc = _find_column(headers_norm, "counterparty_account")

    if col_date is None or (col_amount is None and (col_debit is None and col_credit is None)):
        return {"extraction_method": "csv_unrecognized",
                "warnings": ["Colonnes date/montant absentes"],
                "transactions": []}

    txns = []
    warnings = []
    for i, r in enumerate(data_rows):
        def get(idx):
            return r[idx].strip() if (idx is not None and idx < len(r)) else ""
        d = _parse_date(get(col_date))
        if not d:
            warnings.append(f"Ligne {i + header_idx + 2} : date invalide, ignoree")
            continue
        if col_amount is not None:
            amt = _parse_amount(get(col_amount))
        else:
            dbt = _parse_amount(get(col_debit)) or 0.0
            crd = _parse_amount(get(col_credit)) or 0.0
            amt = round(crd - abs(dbt), 2)
        if amt is None:
            warnings.append(f"Ligne {i + header_idx + 2} : montant invalide, ignoree")
            continue
        txns.append({
            "date": d,
            "amount": round(float(amt), 2),
            "transaction_type": "credit" if amt >= 0 else "debit",
            "counterparty_name": get(col_cp),
            "counterparty_account": get(col_cp_acc),
            "communication": get(col_comm),
        })
    if not txns:
        return {"extraction_method": "csv_unrecognized",
                "warnings": warnings + ["Aucune transaction extraite"],
                "transactions": []}
    return {
        "extraction_method": "csv_smart",
        "account_number": "",
        "period_from": txns[0]["date"] if txns else "",
        "period_to": txns[-1]["date"] if txns else "",
        "opening_balance": 0.0,
        "closing_balance": 0.0,
        "transactions": txns,
        "warnings": warnings,
    }


# ---- LLM Vision (Gemini) ----


_LLM_SYSTEM_MESSAGE = (
    "Tu es un expert-comptable belge charge d'extraire des transactions "
    "bancaires depuis des extraits de compte (PDF ou CSV) de banques belges "
    "(BNP Paribas Fortis, Belfius, KBC, ING, ...). Tu renvoies STRICTEMENT "
    "du JSON valide sans texte autour, sans markdown, sans ```json."
)

_LLM_USER_PROMPT = """Extrait TOUTES les transactions du document. Renvoie strictement ce JSON (aucun texte autour) :

{
  "account_number": "IBAN complet BE... ou chaine vide",
  "period_from": "YYYY-MM-DD ou chaine vide",
  "period_to": "YYYY-MM-DD ou chaine vide",
  "opening_balance": nombre en EUR (0 si inconnu),
  "closing_balance": nombre en EUR (0 si inconnu),
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "amount": nombre signe (+ credit / - debit),
      "counterparty_name": "nom contrepartie ou chaine vide",
      "counterparty_account": "IBAN contrepartie ou chaine vide",
      "communication": "message / libelle / reference"
    }
  ]
}

Regles :
- amount : signe positif si credit (entree d'argent), negatif si debit (sortie).
  Astuce : dans les extraits Fortis, le montant est suivi de "+" (credit) ou "-" (debit).
- date : format ISO YYYY-MM-DD, date de valeur si disponible sinon date operation.
- Ne pas inclure les lignes de solde initial / final / total / report.
- Regrouper les lignes multi-lignes (date, numero de mouvement, communication, reference banque) en UNE seule transaction.
- Ne pas ajouter de champs supplementaires. Aucun commentaire, aucun markdown."""


def _extract_pdf_text_ocr(file_path: str) -> str:
    """Fallback OCR pour PDFs scannes (sans couche texte).

    - Rasterise chaque page via PyMuPDF (fitz) a 300 DPI (compromis qualite / RAM).
    - Applique Tesseract avec les langues fra+eng (extraits bancaires belges).
    - Retourne le texte concatene de toutes les pages. Vide si tout echoue.
    """
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io as _io
    except Exception as e:
        raise RuntimeError(f"OCR indisponible (dependances manquantes) : {e}")

    parts: List[str] = []
    try:
        doc = fitz.open(file_path)
        try:
            # 300 DPI (zoom = 300/72 ≈ 4.17) : compromis qualite OCR / RAM.
            # Extraits bancaires typiques = 1-3 pages, RAM reste raisonnable.
            zoom = 300 / 72
            mat = fitz.Matrix(zoom, zoom)
            for page in doc:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.open(_io.BytesIO(pix.tobytes("png")))
                # PSM 6 = bloc uniforme (tableaux bancaires); langues FR+EN.
                txt = pytesseract.image_to_string(img, lang="fra+eng", config="--psm 6") or ""
                if txt.strip():
                    parts.append(txt)
        finally:
            doc.close()
    except Exception as e:
        raise RuntimeError(f"Echec OCR Tesseract : {e}")

    return "\n".join(parts).strip()


def _extract_pdf_text(file_path: str) -> str:
    """Extraction texte via pdfplumber (tableaux bancaires optimaux).
    Fallback pypdf si pdfplumber echoue. Si le PDF n'a aucune couche texte
    (typiquement scan), bascule automatiquement sur l'OCR Tesseract.
    Retourne texte concatene multi-pages."""
    text_parts: List[str] = []
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t:
                    text_parts.append(t)
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            for page in reader.pages:
                t = page.extract_text() or ""
                if t:
                    text_parts.append(t)
        except Exception as e:
            raise RuntimeError(f"Impossible d'extraire le texte du PDF : {e}")
    joined = "\n".join(text_parts).strip()
    # Heuristique : moins de 40 caracteres utiles -> le PDF est probablement
    # un scan sans couche texte. On tente l'OCR Tesseract.
    if len(joined) < 40:
        try:
            ocr_text = _extract_pdf_text_ocr(file_path)
            if ocr_text and len(ocr_text) > len(joined):
                return ocr_text
        except Exception:
            # OCR indisponible ou en erreur : on retourne ce qu'on a
            pass
    return joined


async def parse_with_llm(file_path: str, mime_type: str) -> Dict[str, Any]:
    """Extraction LLM text-only via Claude Sonnet 4.5 pour PDF ou CSV non
    reconnu. La cle EMERGENT_LLM_KEY autorise Claude text uniquement (pas
    Gemini Vision), donc :
    - PDF : on extrait le texte via pdfplumber avant l'envoi au LLM.
    - CSV : le contenu texte est envoye directement.
    Retour : meme structure que parse_csv_smart, extraction_method='llm_text'."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    api_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY absent - impossible d'extraire avec IA")

    # 1) Preparer le texte a envoyer au LLM
    used_ocr = False
    if mime_type == "application/pdf" or file_path.lower().endswith(".pdf"):
        # Tentative extraction texte normale (pdfplumber -> pypdf -> OCR fallback)
        source_text = _extract_pdf_text(file_path)
        if not source_text:
            raise RuntimeError(
                "PDF sans texte extractible meme apres OCR. "
                "Verifiez que le fichier n'est pas vide ou corrompu."
            )
        # Detection : si le PDF n'avait pas de couche texte, _extract_pdf_text
        # a bascule sur OCR. On refait un check rapide pour tagger l'origine.
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as _pdf:
                _plain = "\n".join((p.extract_text() or "") for p in _pdf.pages).strip()
            if len(_plain) < 40:
                used_ocr = True
        except Exception:
            pass
        source_label = "extrait de compte PDF" + (" (OCR)" if used_ocr else "")
    else:
        # CSV / texte brut
        with open(file_path, "rb") as fh:
            raw = fh.read()
        source_text = None
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                source_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if source_text is None:
            raise RuntimeError("Impossible de decoder le fichier CSV")
        source_label = "extrait de compte CSV"

    # Limite de securite (~120K caracteres = ~30K tokens, marge confortable
    # pour Claude Sonnet 200K context).
    if len(source_text) > 120000:
        source_text = source_text[:120000] + "\n[...TRONQUE...]"

    session_id = f"bank-import-{uuid.uuid4().hex[:12]}"
    chat = LlmChat(
        api_key=api_key,
        session_id=session_id,
        system_message=_LLM_SYSTEM_MESSAGE,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    user_text = (
        _LLM_USER_PROMPT
        + f"\n\n--- CONTENU DU {source_label.upper()} ---\n"
        + source_text
    )
    resp = await chat.send_message(UserMessage(text=user_text))
    text = (resp or "").strip()
    # Nettoyer d'eventuels blocs markdown ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise RuntimeError(f"Reponse IA non-JSON : {text[:300]}")
        data = json.loads(m.group(0))

    # Normalisation
    txns = []
    for t in data.get("transactions", []) or []:
        d = _parse_date(t.get("date"))
        amt = _parse_amount(t.get("amount"))
        if not d or amt is None:
            continue
        txns.append({
            "date": d,
            "amount": round(float(amt), 2),
            "transaction_type": "credit" if amt >= 0 else "debit",
            "counterparty_name": (t.get("counterparty_name") or "").strip(),
            "counterparty_account": (t.get("counterparty_account") or "").strip(),
            "communication": (t.get("communication") or "").strip(),
        })
    warnings = [] if txns else ["IA n'a extrait aucune transaction"]
    if used_ocr:
        warnings.append(
            "PDF scanne detecte : extraction via OCR Tesseract (fra+eng). "
            "Verifiez chaque transaction, la reconnaissance de caracteres peut "
            "introduire des erreurs sur montants ou dates."
        )
    return {
        "extraction_method": "llm_text_ocr" if used_ocr else "llm_text",
        "account_number": (data.get("account_number") or "").strip(),
        "period_from": _parse_date(data.get("period_from")) or "",
        "period_to": _parse_date(data.get("period_to")) or "",
        "opening_balance": float(_parse_amount(data.get("opening_balance")) or 0),
        "closing_balance": float(_parse_amount(data.get("closing_balance")) or 0),
        "transactions": txns,
        "warnings": warnings,
    }


async def extract_bank_statement(content: bytes, filename: str, mime_type: str,
                                 tmp_path: str) -> Dict[str, Any]:
    """Dispatcher principal : selon le type de fichier, choisit la strategie.
    - PDF : LLM Vision direct.
    - CSV : parser smart -> fallback LLM si non reconnu.
    tmp_path : chemin absolu du fichier deja ecrit sur disque (pour LlmChat).
    """
    lower = (filename or "").lower()
    if lower.endswith(".pdf") or mime_type == "application/pdf":
        return await parse_with_llm(tmp_path, "application/pdf")
    if lower.endswith(".csv") or "csv" in (mime_type or ""):
        res = parse_csv_smart(content, filename)
        if res.get("extraction_method") == "csv_unrecognized" or not res.get("transactions"):
            # Fallback IA
            try:
                llm_res = await parse_with_llm(tmp_path, "text/csv")
                llm_res["extraction_method"] = "csv_llm_fallback"
                # Merger warnings
                llm_res["warnings"] = (res.get("warnings", []) or []) + llm_res.get("warnings", [])
                return llm_res
            except Exception as e:
                res["warnings"] = (res.get("warnings", []) or []) + [f"Fallback IA a echoue : {e}"]
                return res
        return res
    raise RuntimeError(f"Type de fichier non supporte : {filename} ({mime_type})")
