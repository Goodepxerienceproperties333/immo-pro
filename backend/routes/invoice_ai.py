"""Endpoint dedie : upload facture fournisseur PDF -> extraction IA Claude -> pre-remplit le formulaire.

iter87 : ce endpoint ne persiste plus rien sur disque. Le PDF temporaire est
ecrit dans un NamedTemporaryFile (auto-clean) le temps de l'extraction IA,
puis supprime. Aucune trace sur le filesystem.

iter90an : accelere la reconnaissance IA :
- Path texte -> Claude Haiku 4.5 (3-4x plus rapide que Sonnet 4.5 pour du JSON).
- Path vision (scans) -> reste sur Sonnet 4.5 (qualite critique OCR visuel).
- max_chars 8000 -> 4000 (2 pages A4 suffisent pour une facture belge).
- Liste PCMN 60 -> 40 comptes classe 6 (moins de tokens en input).
- Post-processing PCMN check + supplier match parallelises via asyncio.gather.
- Cache PCMN par ACP en memoire (TTL 5 min) -> evite le double round-trip Mongo.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from pathlib import Path
import asyncio
import os
import json
import time
import uuid
import tempfile


# iter90an : cache PCMN par ACP (TTL 5 min) - evite refetch a chaque extraction.
# Cle : copropriete_id (ou "" pour global). Valeur : (ts, [{"number","name"}], set(numbers))
_PCMN_CACHE: dict = {}
_PCMN_TTL_S = 300


async def _get_pcmn_cached(db, copropriete_id: str) -> tuple[list, set]:
    """Return (class-6 list [{number,name}], full valid_accs set) with 5-min TTL cache."""
    key = copropriete_id or ""
    now = time.time()
    hit = _PCMN_CACHE.get(key)
    if hit and (now - hit[0]) < _PCMN_TTL_S:
        return hit[1], hit[2]
    q = {"class_num": 6}
    if copropriete_id:
        q["copropriete_id"] = copropriete_id
    # 40 comptes classe 6 = suffisant pour Belgium ACPs (charges courantes).
    class6 = await db.pcmn_accounts.find(q, {"_id": 0, "number": 1, "name": 1}).sort("number", 1).to_list(40)
    # Full set toutes classes pour validation des lignes AI.
    q2 = {}
    if copropriete_id:
        q2["copropriete_id"] = copropriete_id
    all_docs = await db.pcmn_accounts.find(q2, {"_id": 0, "number": 1}).to_list(2000)
    valid = {p["number"] for p in all_docs if p.get("number")}
    _PCMN_CACHE[key] = (now, class6, valid)
    return class6, valid


async def _extract_pdf_text(file_path: str, max_chars: int = 4000) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            if len(text) > max_chars:
                break
        return text[:max_chars].strip()
    except Exception:
        return ""


def _norm_bce(v: str) -> str:
    """Normalise un numero BCE/TVA en 9-10 chiffres pour comparaison exacte."""
    if not v:
        return ""
    digits = "".join(c for c in v if c.isdigit())
    return digits[-10:] if len(digits) >= 9 else digits


async def _get_supplier_bce_index(db, copropriete_id: str) -> dict:
    """Index {bce/vat normalise -> fiche supplier}, SCOPE a cette copropriete
    (+ fiches globales). Remplace 2 scans complets et non-filtres de la
    collection `suppliers` (TOUTE la base multi-tenant, tous les clients de
    la plateforme) par UNE seule requete bornee, evaluee une fois par
    extraction au lieu de 2 fois. Meme classe de bug que le crash P0 du
    dashboard (health_audit.py) : un scan multi-tenant non filtre execute a
    chaque requete utilisateur degrade/timeout au fur et a mesure que la
    plateforme grossit (l'IA d'extraction de facture est appelee bien plus
    souvent qu'un chargement de dashboard).
    """
    q = {"$or": [
        {"bce_number": {"$exists": True, "$ne": ""}},
        {"vat_number": {"$exists": True, "$ne": ""}},
    ]}
    if copropriete_id:
        q = {"$and": [q, {"$or": [
            {"copropriete_id": copropriete_id},
            {"is_global": True},
            {"copropriete_id": {"$in": [None, ""]}},
        ]}]}
    suppliers = await db.suppliers.find(q, {"_id": 0}).to_list(2000)
    index = {}
    for s in suppliers:
        sb = _norm_bce(s.get("bce_number", "") or s.get("vat_number", ""))
        if sb:
            index[sb] = s
    return index


async def _extract_invoice_with_ai(file_path: str, mime_type: str, known_pcmn: list) -> dict:
    """Use Claude Sonnet 4.5 to extract invoice metadata from PDF.
    - Tries text extraction first (fast).
    - Falls back to Claude vision (PDF binary) if text empty (scan-image PDFs).
    Returns:
    - dict with extracted fields on success
    - dict with `_warning` key when extraction failed
    """
    if mime_type != "application/pdf":
        return {"_warning": "Format non supporte (PDF uniquement pour l'extraction IA)"}

    text = await _extract_pdf_text(file_path)
    use_vision = not text  # scan-image PDF without OCR layer

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContent
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            return {"_warning": "Cle LLM Emergent non configuree, extraction IA indisponible"}
        pcmn_hint = "\n".join(f"  {p['number']} - {p['name']}" for p in known_pcmn[:60])
        chat = LlmChat(
            api_key=api_key,
            session_id=f"invoice-{uuid.uuid4().hex[:8]}",
            system_message=(
                "You are an accounting AI for Belgian condominium management. "
                "Extract invoice metadata as STRICT JSON, no markdown, no code fences.\n"
                "Schema: {"
                '"supplier_name": "<exact supplier name>", '
                '"number": "<invoice number>", '
                '"date": "<YYYY-MM-DD, invoice issue date>", '
                '"due_date": "<YYYY-MM-DD, payment due date, empty if not present>", '
                '"total_amount": <float TTC>, '
                '"vat_amount": <float VAT amount>, '
                '"net_amount": <float HT>, '
                '"vat_rate": <float, e.g. 21 or 6>, '
                '"description": "<short description of service/goods>", '
                '"suggested_pcmn_account": "<6-digit account from list, best match for whole invoice>", '
                '"vat_number": "<supplier VAT BE0xxx.xxx.xxx or empty>", '
                '"bce_number": "<supplier BCE/CBE/KBO number, e.g. 0123.456.789 or BE0123456789 or empty>", '
                '"iban": "<supplier IBAN or empty>", '
                '"communication": "<structured comm or empty>", '
                '"lines": [<line objects, see below>]'
                "}\n\n"
                "CRITICAL - BELGIAN DATE FORMAT RULES:\n"
                "- Belgian invoices ALWAYS use DAY/MONTH/YEAR format (DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY).\n"
                "- NEVER assume US format (MM/DD/YYYY). If you see '05/06/2026', it means 5 June 2026, NOT May 6.\n"
                "- French month names : janvier=01, fevrier=02, mars=03, avril=04, mai=05, juin=06, "
                "  juillet=07, aout=08, septembre=09, octobre=10, novembre=11, decembre=12.\n"
                "- Dutch month names : januari=01, februari=02, maart=03, april=04, mei=05, juni=06, "
                "  juli=07, augustus=08, september=09, oktober=10, november=11, december=12.\n"
                "- If the year is 2-digit (e.g. '15/06/25'), assume 20XX (2025).\n"
                "- Output MUST be ISO 8601: YYYY-MM-DD (e.g. 2026-06-15). NEVER other formats.\n"
                "- If the invoice shows MULTIPLE dates (invoice date, due date, service period, "
                "  reference date), pick :\n"
                "  * `date` = the invoice ISSUE date (usually labeled 'Date facture', 'Datum factuur', "
                "    'Invoice date', 'Facture du', 'Date d'emission', 'Factuurdatum').\n"
                "  * `due_date` = the PAYMENT deadline (labeled 'Echeance', 'A payer avant', "
                "    'Vervaldatum', 'Vervaldag', 'Payment due', 'Date d'echeance').\n"
                "- If NO clear date is found, return empty string \"\" for that field. DO NOT invent.\n\n"
                "Each detail line in `lines` MUST have shape: "
                '{"description": "<short label of the line>", '
                '"amount": <float TTC for this line>, '
                '"suggested_pcmn_account": "<best-match 6-digit account from list>"'
                "}\n\n"
                "Rules for lines extraction:\n"
                "- If the invoice has SEVERAL detail rows/postes (different services or goods), "
                "  return one object per row, each with its own amount and best-matched account.\n"
                "- If the invoice has only ONE detail row, return lines=[] (empty array) - "
                "  do NOT duplicate the single total as a one-element array.\n"
                "- The sum of lines[].amount MUST equal total_amount (within 0.01 EUR tolerance).\n"
                "- Match each line's account independently (e.g. honoraires syndic -> 612xxx, "
                "  frais admin -> 612xxx or 613xxx, entretien -> 611xxx, electricite -> 612xxx, etc.).\n\n"
                "Available PCMN accounts (class 6 only, choose the most appropriate):\n"
                f"{pcmn_hint}\n"
                "Use 0 or empty strings if unknown. Never invent values."
            ),
        # iter90ap : Sonnet 4.6 pour le path texte (recommande, meilleur ratio
        # vitesse/precision que Haiku 4.5 - dates belges ambigues necessitent
        # plus de "raisonnement" que le format US par defaut).
        # Sonnet 4.5 conserve pour la vision (OCR de PDF scanne, qualite critique).
        ).with_model(
            "anthropic",
            "claude-sonnet-4-5-20250929" if use_vision else "claude-sonnet-4-6",
        )

        if use_vision:
            # Send the PDF as a file attachment (Claude vision parses scan-image PDFs)
            import base64
            with open(file_path, "rb") as fpdf:
                pdf_b64 = base64.b64encode(fpdf.read()).decode("ascii")
            file_content = FileContent(
                content_type="application/pdf",
                file_content_base64=pdf_b64,
            )
            msg = UserMessage(
                text=(
                    "Extract metadata from this Belgian supplier invoice (PDF is a "
                    "scanned image, please OCR it visually). Output the strict JSON "
                    "schema described in the system message."
                ),
                file_contents=[file_content],
            )
        else:
            msg = UserMessage(text=f"Extract metadata from this Belgian supplier invoice:\n\n{text}")

        # P0-class fix (iter90fp) : garde-fou anti-timeout sur l'appel LLM.
        # Un PDF scanne volumineux (path vision) ou une latence provider
        # peut faire trainer cet appel bien au-dela du timeout du reverse
        # proxy (Cloudflare) -> connexion coupee brutalement, l'utilisateur
        # voit une erreur opaque au lieu d'un message clair. On borne
        # l'appel a 55s et on degrade proprement (formulaire vide + message
        # explicite, saisie manuelle toujours possible) au lieu de laisser
        # la requete pendre.
        response = await asyncio.wait_for(chat.send_message(msg), timeout=55.0)
        txt = response.strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1] if "```" in txt[3:] else txt[3:]
            if txt.startswith("json"):
                txt = txt[4:]
            txt = txt.strip("` \n")
        return json.loads(txt)
    except asyncio.TimeoutError:
        print("[AI invoice extract skipped]: LLM timeout > 55s")
        return {"_warning": "Extraction IA trop lente (>55s), veuillez remplir manuellement ou reessayer"}
    except Exception as e:
        print(f"[AI invoice extract skipped]: {e}")
        return {"_warning": f"Echec extraction IA : {str(e)[:200]}"}


def create_invoice_ai_router(db):
    router = APIRouter(prefix="/api/invoices-ai")

    @router.post("/extract")
    async def extract_invoice(
        file: UploadFile = File(...),
        copropriete_id: Optional[str] = Form(""),
    ):
        """Upload a PDF supplier invoice and return AI-extracted fields (no DB persistence).

        iter87 : the PDF is written to a NamedTemporaryFile during extraction
        and deleted right after. Nothing is persisted on disk or in GridFS
        (the extraction is stateless - the user will upload the PDF again
        if they want to attach it to a real invoice).
        """
        ext = Path(file.filename or "file").suffix.lower()
        if ext != ".pdf":
            raise HTTPException(400, "PDF requis pour l'extraction IA")
        content = await file.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        raw_text = ""  # iter90aq : conserve pour le "learn" post-save
        supplier_id_guess = None
        supplier_name_guess = ""
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()

            # iter90an : PCMN via cache TTL (evite refetch a chaque extraction)
            pcmn, valid_accs = await _get_pcmn_cached(db, copropriete_id or "")
            # iter90fp : index BCE/TVA scope a CETTE copropriete, calcule UNE
            # SEULE fois par extraction (reutilise plus bas dans _find_supplier
            # au lieu de re-scanner toute la collection une 2e fois).
            supplier_bce_index = await _get_supplier_bce_index(db, copropriete_id or "")

            # iter90aq : template appris - extraction rapide sans IA si supplier connu.
            # 1) Extraire le texte brut du PDF
            raw_text = await _extract_pdf_text(tmp.name, max_chars=6000)
            # 2) Deviner le supplier via BCE/VAT (bien plus fiable que le nom)
            template_result = None
            if raw_text and copropriete_id:
                from routes.invoice_templates import try_apply_supplier_template
                import re as _re
                bce_matches = _re.findall(
                    r"BE\s*0?\d{3}[.\s]?\d{3}[.\s]?\d{3}|0\d{3}[.\s]\d{3}[.\s]\d{3}",
                    raw_text,
                )
                for bce_raw in bce_matches:
                    bce_digits = "".join(c for c in bce_raw if c.isdigit())
                    if len(bce_digits) < 9:
                        continue
                    bce_norm = bce_digits[-10:] if len(bce_digits) >= 10 else bce_digits
                    _s = supplier_bce_index.get(bce_norm)
                    if _s:
                        supplier_id_guess = _s.get("id")
                        supplier_name_guess = _s.get("name", "")
                        break

                if supplier_id_guess:
                    template_result = await try_apply_supplier_template(
                        db, supplier_id_guess, copropriete_id or "", raw_text,
                    )

            # 3) Si le template a rempli les 3 champs critiques -> skip IA
            crit_fields = {"number", "date", "total_amount"}
            template_fields = (template_result or {}).get("fields", {}) if template_result else {}
            template_covers_critical = crit_fields.issubset(template_fields.keys()) if template_fields else False

            if template_covers_critical:
                # Extraction rapide par template - pas d'appel IA
                result = {
                    "supplier_name": supplier_name_guess,
                    "number": template_fields.get("number", ""),
                    "date": template_fields.get("date", ""),
                    "due_date": template_fields.get("due_date", ""),
                    "total_amount": template_fields.get("total_amount", 0),
                    "vat_amount": template_fields.get("vat_amount", 0),
                    "net_amount": template_fields.get("net_amount", 0),
                    "vat_rate": template_fields.get("vat_rate", 0),
                    "iban": template_fields.get("iban", ""),
                    "communication": template_fields.get("communication", ""),
                    "description": "",
                    "suggested_pcmn_account": "",
                    "vat_number": "",
                    "bce_number": "",
                    "lines": [],
                    "_extraction_source": "template",
                    "_template_id": template_result.get("template_id", ""),
                }
            else:
                # Fallback : appel Claude Sonnet 4.6 classique
                result = await _extract_invoice_with_ai(tmp.name, "application/pdf", pcmn)
                result["_extraction_source"] = "ai"
                # Injecter les champs du template en priorite sur ce que l'IA a extrait
                if template_fields:
                    for k, v in template_fields.items():
                        if v not in (None, "", 0):
                            result[k] = v
                    result["_template_partial"] = list(template_fields.keys())
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        # iter90an : parallelise 2 lookups DB (PCMN suggested + supplier match).
        async def _check_suggested_pcmn():
            acc = result.get("suggested_pcmn_account")
            if not acc:
                return
            # Utilise le set du cache : lookup en memoire O(1), pas de round-trip Mongo.
            if acc not in valid_accs:
                result["suggested_pcmn_account"] = ""

        # iter90ap : validation post-extraction des dates (sanity check).
        # Signale les dates aberrantes (annee < 2020 ou > 2035, format non ISO,
        # due_date < date). Ne bloque pas la creation, mais avertit l'UI via
        # `_date_warning` pour que le syndic verifie manuellement.
        def _validate_dates():
            from datetime import date as _dt_cls
            warnings = []
            date_val = (result.get("date") or "").strip()
            due_val = (result.get("due_date") or "").strip()
            parsed_date = None
            parsed_due = None
            if date_val:
                try:
                    parsed_date = _dt_cls.fromisoformat(date_val)
                    y = parsed_date.year
                    if y < 2020 or y > 2035:
                        warnings.append(f"Date facture suspecte ({date_val}) : annee hors plage 2020-2035")
                        result["date"] = ""
                except (ValueError, TypeError):
                    warnings.append(f"Date facture illisible : '{date_val}' (format non ISO YYYY-MM-DD)")
                    result["date"] = ""
            if due_val:
                try:
                    parsed_due = _dt_cls.fromisoformat(due_val)
                    y = parsed_due.year
                    if y < 2020 or y > 2035:
                        warnings.append(f"Date echeance suspecte ({due_val}) : annee hors plage 2020-2035")
                        result["due_date"] = ""
                except (ValueError, TypeError):
                    warnings.append(f"Date echeance illisible : '{due_val}' (format non ISO YYYY-MM-DD)")
                    result["due_date"] = ""
            # Coherence : due_date >= date
            if parsed_date and parsed_due and parsed_due < parsed_date:
                warnings.append(
                    f"Incoherence : echeance ({due_val}) anterieure a date facture ({date_val}). "
                    f"L'IA a probablement inverse jour/mois - a verifier."
                )
            if warnings:
                result["_date_warning"] = " ; ".join(warnings)

        _validate_dates()

        bce_norm = _norm_bce(result.get("bce_number", "") or result.get("vat_number", ""))
        result["bce_normalized"] = bce_norm

        async def _find_supplier():
            supplier_match = None
            match_method = None
            if bce_norm:
                # iter90fp : reutilise l'index deja calcule (1 seul scan par
                # extraction, scope a cette copropriete) au lieu d'un 2e scan
                # complet non filtre de toute la base multi-tenant.
                _s = supplier_bce_index.get(bce_norm)
                if _s:
                    supplier_match = _s
                    match_method = "bce"
            if not supplier_match and result.get("supplier_name"):
                name_q = {"name": {"$regex": result["supplier_name"], "$options": "i"}}
                if copropriete_id:
                    name_q = {"$and": [name_q, {"$or": [
                        {"copropriete_id": copropriete_id},
                        {"is_global": True},
                        {"copropriete_id": {"$in": [None, ""]}},
                    ]}]}
                supplier_match = await db.suppliers.find_one(name_q, {"_id": 0})
                if supplier_match:
                    match_method = "name"
            return supplier_match, match_method

        # Lancer PCMN check + supplier match en parallele
        _, (supplier_match, match_method) = await asyncio.gather(
            _check_suggested_pcmn(),
            _find_supplier(),
        )

        # Validate AI-extracted lines (if present): each line must have a valid
        # suggested account, and the sum of amounts must match total (0.01 tolerance).
        raw_lines = result.get("lines") or []
        if isinstance(raw_lines, list) and len(raw_lines) > 1:
            # iter90an : valid_accs deja en memoire via cache
            cleaned = []
            for ln in raw_lines:
                if not isinstance(ln, dict):
                    continue
                try:
                    amt = float(ln.get("amount", 0) or 0)
                except (TypeError, ValueError):
                    amt = 0
                if amt <= 0:
                    continue
                acc = (ln.get("suggested_pcmn_account") or "").strip()
                if acc and acc not in valid_accs:
                    acc = ""  # invalid, let user pick
                cleaned.append({
                    "description": (ln.get("description") or "").strip(),
                    "amount": round(amt, 2),
                    "suggested_pcmn_account": acc,
                })
            # Verify sum coherence
            total = float(result.get("total_amount", 0) or 0)
            line_sum = round(sum(item["amount"] for item in cleaned), 2)
            if cleaned and abs(line_sum - total) > 0.01:
                # Mismatch -> drop lines (single-line fallback)
                cleaned = []
            result["lines"] = cleaned
        else:
            result["lines"] = []  # Single-line invoice -> empty lines

        # Suggestion creation : si pas de match ET on a un nom
        suggest_create = (not supplier_match) and bool(result.get("supplier_name"))

        return {
            "extracted": result,
            "supplier_match": supplier_match,
            "supplier_match_method": match_method,
            "supplier_suggest_create": suggest_create,
            "filename": file.filename,
            # iter87 : no on-disk path anymore (tempfile cleaned). Caller
            # re-uploads the PDF later via /invoices/{id}/attachments if needed.
            "stored_temp_path": "",
            # iter90aq : texte brut renvoye au frontend pour le "learn" post-save
            # (frontend le renvoie via POST /invoice-templates/learn quand user save).
            "raw_text": raw_text,
            "supplier_id_guess": supplier_id_guess or "",
        }

    return router
