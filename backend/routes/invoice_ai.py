"""Endpoint dedie : upload facture fournisseur PDF -> extraction IA Claude -> pre-remplit le formulaire."""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from pathlib import Path
import os
import json
import uuid

UPLOAD_DIR = Path("/app/uploads/invoices")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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
    """Use Claude Sonnet 4.5 to extract invoice metadata from PDF."""
    if mime_type != "application/pdf":
        return {}
    text = await _extract_pdf_text(file_path)
    if not text:
        return {}
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            return {}
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
                '"suggested_pcmn_account": "<6-digit account from this list, best match>", '
                '"vat_number": "<supplier VAT BE0xxx.xxx.xxx or empty>", '
                '"iban": "<supplier IBAN or empty>", '
                '"communication": "<structured comm or empty>"'
                "}\n\n"
                "Available PCMN accounts (class 6 only, choose the most appropriate):\n"
                f"{pcmn_hint}\n"
                "Use 0 or empty strings if unknown. Date format ISO YYYY-MM-DD only."
            ),
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")

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
        return {}


def create_invoice_ai_router(db):
    router = APIRouter(prefix="/api/invoices-ai")

    @router.post("/extract")
    async def extract_invoice(
        file: UploadFile = File(...),
        copropriete_id: Optional[str] = Form(""),
    ):
        """Upload a PDF supplier invoice and return AI-extracted fields (no DB persistence)."""
        ext = Path(file.filename or "file").suffix.lower()
        if ext != ".pdf":
            raise HTTPException(400, "PDF requis pour l'extraction IA")
        tmp_id = uuid.uuid4().hex
        file_path = UPLOAD_DIR / f"{tmp_id}.pdf"
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Get PCMN accounts (class 6) for this ACP to inform AI
        q = {"class_num": 6}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        pcmn = await db.pcmn_accounts.find(q, {"_id": 0, "number": 1, "name": 1}).sort("number", 1).to_list(200)

        result = await _extract_invoice_with_ai(str(file_path), "application/pdf", pcmn)

        # Verify suggested PCMN exists; if not, blank it
        if result.get("suggested_pcmn_account"):
            exists = await db.pcmn_accounts.find_one({"number": result["suggested_pcmn_account"], "copropriete_id": copropriete_id or {"$exists": True}}, {"_id": 0})
            if not exists:
                result["suggested_pcmn_account"] = ""

        # Match supplier in DB by name fuzzy
        supplier_match = None
        if result.get("supplier_name"):
            supplier_match = await db.suppliers.find_one(
                {"name": {"$regex": result["supplier_name"], "$options": "i"}}, {"_id": 0}
            )

        return {
            "extracted": result,
            "supplier_match": supplier_match,
            "filename": file.filename,
            "stored_temp_path": str(file_path),
        }

    return router
