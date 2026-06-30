"""Endpoint dedie : upload facture fournisseur PDF -> extraction IA Claude -> pre-remplit le formulaire.

iter87 : ce endpoint ne persiste plus rien sur disque. Le PDF temporaire est
ecrit dans un NamedTemporaryFile (auto-clean) le temps de l'extraction IA,
puis supprime. Aucune trace sur le filesystem.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from pathlib import Path
import os
import json
import uuid
import tempfile


async def _extract_pdf_text(file_path: str, max_chars: int = 8000) -> str:
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
                '"date": "<YYYY-MM-DD>", '
                '"due_date": "<YYYY-MM-DD or empty>", '
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
                "Each detail line in `lines` MUST have shape: "
                '{"description": "<short label of the line>", '
                '"amount": <float TTC for this line>, '
                '"suggested_pcmn_account": "<best-match 6-digit account from list>"'
                "}\n\n"
                "Rules for lines extraction:\n"
                "- If the invoice has SEVERAL detail rows/postes (different services or goods), "
                "  return one object per row, each with its own amount and best-matched account.\n"
                "- If the invoice has only ONE detail row, return lines=[] (empty array) — "
                "  do NOT duplicate the single total as a one-element array.\n"
                "- The sum of lines[].amount MUST equal total_amount (within 0.01 EUR tolerance).\n"
                "- Match each line's account independently (e.g. honoraires syndic -> 612xxx, "
                "  frais admin -> 612xxx or 613xxx, entretien -> 611xxx, electricite -> 612xxx, etc.).\n\n"
                "Available PCMN accounts (class 6 only, choose the most appropriate):\n"
                f"{pcmn_hint}\n"
                "Use 0 or empty strings if unknown. Date format ISO YYYY-MM-DD only."
            ),
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")

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

        response = await chat.send_message(msg)
        txt = response.strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1] if "```" in txt[3:] else txt[3:]
            if txt.startswith("json"):
                txt = txt[4:]
            txt = txt.strip("` \n")
        return json.loads(txt)
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
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()

            # Get PCMN accounts (class 6) for this ACP to inform AI
            q = {"class_num": 6}
            if copropriete_id:
                q["copropriete_id"] = copropriete_id
            pcmn = await db.pcmn_accounts.find(q, {"_id": 0, "number": 1, "name": 1}).sort("number", 1).to_list(200)

            result = await _extract_invoice_with_ai(tmp.name, "application/pdf", pcmn)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        # Verify suggested PCMN exists; if not, blank it
        if result.get("suggested_pcmn_account"):
            exists = await db.pcmn_accounts.find_one({"number": result["suggested_pcmn_account"], "copropriete_id": copropriete_id or {"$exists": True}}, {"_id": 0})
            if not exists:
                result["suggested_pcmn_account"] = ""

        # Validate AI-extracted lines (if present): each line must have a valid
        # suggested account, and the sum of amounts must match total (0.01 tolerance).
        raw_lines = result.get("lines") or []
        if isinstance(raw_lines, list) and len(raw_lines) > 1:
            # Pre-fetch valid PCMN account numbers for this ACP
            valid_accs = set()
            async for p in db.pcmn_accounts.find(
                {"copropriete_id": copropriete_id or {"$exists": True}},
                {"_id": 0, "number": 1},
            ):
                valid_accs.add(p["number"])

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

        # Normalize BCE/VAT numbers (strip dots, spaces; uppercase prefix)
        def _norm_bce(v: str) -> str:
            if not v:
                return ""
            digits = "".join(c for c in v if c.isdigit())
            return digits[-10:] if len(digits) >= 9 else digits

        bce_norm = _norm_bce(result.get("bce_number", "") or result.get("vat_number", ""))
        result["bce_normalized"] = bce_norm

        # Match supplier : 1) BCE/VAT 2) Nom
        supplier_match = None
        match_method = None
        if bce_norm:
            cursor = db.suppliers.find({"$or": [
                {"bce_number": {"$exists": True, "$ne": ""}},
                {"vat_number": {"$exists": True, "$ne": ""}},
            ]}, {"_id": 0})
            async for s in cursor:
                sb = _norm_bce(s.get("bce_number", "") or s.get("vat_number", ""))
                if sb and sb == bce_norm:
                    supplier_match = s
                    match_method = "bce"
                    break
        if not supplier_match and result.get("supplier_name"):
            supplier_match = await db.suppliers.find_one(
                {"name": {"$regex": result["supplier_name"], "$options": "i"}}, {"_id": 0}
            )
            if supplier_match:
                match_method = "name"

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
        }

    return router
