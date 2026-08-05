"""Audit & consolidation des comptes bancaires (Superadmin).

Detecte les ACPs ayant plusieurs comptes PCMN classe 55 pour un meme IBAN
physique (pattern "phantom account" ex. 55114766 + 55176600 pour un meme
BE07732066504766) et permet la consolidation automatique.

Endpoints :
- GET  /api/admin/bank-audit/scan            Analyse toutes les ACP
- POST /api/admin/bank-audit/consolidate/{acp_id}   Consolide une ACP
"""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
import uuid
from collections import defaultdict


def create_bank_audit_router(db):
    router = APIRouter(prefix="/api/admin/bank-audit")

    async def _sa(request):
        from server import get_current_user, is_superadmin_only
        u = await get_current_user(request)
        if not is_superadmin_only(u.get("role", "")):
            raise HTTPException(403, "Superadmin uniquement")
        return u

    @router.get("/scan")
    async def scan_all(request: Request):
        """Scanne toutes les ACP. Pour chaque ACP, compare les PCMN configures
        (bank_accounts.pcmn_number) vs les PCMN classe 55 reellement utilises
        dans le plan comptable. Retourne les ACP a probleme.
        """
        await _sa(request)
        acps = await db.coproprietes.find({}, {"_id": 0}).to_list(500)
        issues = []
        for a in acps:
            aid = a["id"]
            configured = {(ba.get("pcmn_number") or "").strip(): ba
                          for ba in (a.get("bank_accounts") or []) if ba.get("pcmn_number")}
            pcmns_55 = await db.pcmn_accounts.find(
                {"copropriete_id": aid, "number": {"$regex": "^55[0-9]"}},
                {"_id": 0, "number": 1, "name": 1},
            ).to_list(100)
            # Calcul du solde pour chaque compte 55x
            entries = await db.journal_entries.find(
                {"copropriete_id": aid, "lines.account_number": {"$regex": "^55"}},
            ).to_list(20000)
            balances = defaultdict(float)
            for e in entries:
                if e.get("is_reversal") or e.get("reversed"):
                    continue
                for line in e.get("lines", []):
                    n = line.get("account_number", "")
                    if n.startswith("55"):
                        balances[n] += line.get("debit", 0) - line.get("credit", 0)
            # Comptes fantomes = pcmn en usage mais NON configures
            phantoms = [
                {"number": p["number"], "name": p.get("name", ""),
                 "balance": round(balances.get(p["number"], 0), 2)}
                for p in pcmns_55 if p["number"] not in configured
            ]
            configured_stat = [
                {"number": num, "name": ba.get("label", ""),
                 "iban": ba.get("iban", ""),
                 "account_type": ba.get("account_type", ""),
                 "balance": round(balances.get(num, 0), 2)}
                for num, ba in configured.items()
            ]
            if phantoms:
                issues.append({
                    "acp_id": aid,
                    "acp_name": a.get("name", ""),
                    "configured": configured_stat,
                    "phantoms": phantoms,
                    "phantom_count": len(phantoms),
                    "phantom_total_balance": round(sum(p["balance"] for p in phantoms), 2),
                })
        return {"total_acps": len(acps), "issues_count": len(issues), "issues": issues}

    @router.post("/consolidate/{acp_id}")
    async def consolidate(acp_id: str, payload: dict, request: Request):
        """Consolide les phantoms d'une ACP. Payload attendu :
        {"mappings": [{"source": "55114766", "target": "55176600"}, ...]}

        Rebase toutes les lignes JE, txns, statements + supprime les PCMN
        sources. Retourne un rapport detaille.
        """
        await _sa(request)
        acp = await db.coproprietes.find_one({"id": acp_id})
        if not acp:
            raise HTTPException(404, "ACP non trouvee")
        mappings = payload.get("mappings", [])
        if not mappings:
            raise HTTPException(400, "Aucun mapping fourni")
        configured = {(ba.get("pcmn_number") or "").strip()
                      for ba in (acp.get("bank_accounts") or []) if ba.get("pcmn_number")}
        results = []
        now = datetime.now(timezone.utc).isoformat()
        for m in mappings:
            src = (m.get("source") or "").strip()
            tgt = (m.get("target") or "").strip()
            if not src or not tgt:
                continue
            if tgt not in configured:
                results.append({"source": src, "target": tgt, "skipped": True,
                                "reason": "Cible non configuree dans bank_accounts"})
                continue
            # Fetch target details (IBAN + label)
            tgt_ba = next((ba for ba in (acp.get("bank_accounts") or [])
                           if (ba.get("pcmn_number") or "").strip() == tgt), None)
            tgt_name = (tgt_ba or {}).get("label") or f"Compte {tgt}"
            tgt_iban = (tgt_ba or {}).get("iban", "")
            r1 = await db.journal_entries.update_many(
                {"copropriete_id": acp_id, "lines.account_number": src},
                {"$set": {"lines.$[el].account_number": tgt,
                          "lines.$[el].account_name": tgt_name}},
                array_filters=[{"el.account_number": src}],
            )
            r2 = await db.bank_transactions.update_many(
                {"copropriete_id": acp_id, "account_number": src},
                {"$set": {"account_number": tgt}},
            )
            r3 = await db.bank_statements.update_many(
                {"copropriete_id": acp_id, "account_number": src},
                {"$set": {"account_number": tgt, "iban": tgt_iban or tgt}},
            )
            # Cleanup posting_error si applicable
            await db.bank_transactions.update_many(
                {"copropriete_id": acp_id, "posting_error_iban": src},
                {"$unset": {"posting_error": "", "posting_error_iban": "",
                            "posting_error_at": ""}},
            )
            await db.bank_statements.update_many(
                {"copropriete_id": acp_id, "has_posting_error": True},
                {"$unset": {"has_posting_error": ""}},
            )
            r4 = await db.pcmn_accounts.delete_one(
                {"copropriete_id": acp_id, "number": src},
            )
            results.append({
                "source": src, "target": tgt,
                "je_lines_migrated": r1.modified_count,
                "txns_migrated": r2.modified_count,
                "stmts_migrated": r3.modified_count,
                "pcmn_deleted": r4.deleted_count,
            })
        # Audit log
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": "superadmin",
            "user_email": "admin@copro.be",
            "action": "bank_audit.consolidate",
            "details": {"copropriete_id": acp_id, "acp_name": acp.get("name"),
                        "mappings": mappings, "results": results},
            "timestamp": now,
        })
        return {"acp_id": acp_id, "results": results}

    @router.post("/cleanup-zero-balance/{acp_id}")
    async def cleanup_zero_balance(acp_id: str, request: Request):
        """Supprime tous les PCMN classe 55x d'une ACP qui ne sont ni
        configures dans bank_accounts, ni utilises (solde = 0 et 0 ligne).
        Nettoie les artefacts legacy du plan comptable.
        """
        await _sa(request)
        acp = await db.coproprietes.find_one({"id": acp_id}, {"_id": 0, "bank_accounts": 1, "name": 1})
        if not acp:
            raise HTTPException(404, "ACP non trouvee")
        configured = {(ba.get("pcmn_number") or "").strip()
                      for ba in (acp.get("bank_accounts") or []) if ba.get("pcmn_number")}
        pcmns_55 = await db.pcmn_accounts.find(
            {"copropriete_id": acp_id, "number": {"$regex": "^55[0-9]"}},
            {"_id": 0, "number": 1},
        ).to_list(100)
        deleted = []
        for p in pcmns_55:
            num = p["number"]
            if num in configured:
                continue
            # Verifie qu'il n'a AUCUNE ligne JE
            has_je = await db.journal_entries.count_documents(
                {"copropriete_id": acp_id, "lines.account_number": num}, limit=1,
            )
            if has_je > 0:
                continue
            await db.pcmn_accounts.delete_one({"copropriete_id": acp_id, "number": num})
            deleted.append(num)
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": "superadmin", "user_email": "admin@copro.be",
            "action": "bank_audit.cleanup_zero",
            "details": {"copropriete_id": acp_id, "acp_name": acp.get("name"),
                        "deleted": deleted},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return {"acp_id": acp_id, "deleted_count": len(deleted), "deleted": deleted}

    return router
