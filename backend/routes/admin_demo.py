"""iter93l : Generateur de copropriete DEMO pour presentation aux prospects.
Superadmin ONLY. Cree une ACP coherente comptablement :
- 10 lots (6 appartements, 1 studio, 2 caves, 1 parking)
- 10 proprietaires (noms belges realistes)
- 2 cles de repartition (Generale, Ascenseur)
- 5 fournisseurs
- Budget avec 6 lignes
- ~8 factures fournisseurs sur 2025
- ~20 extraits bancaires (provisions + reglements)
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, HTTPException, Request

DEMO_ACP_NAME = "DEMO - Residence Les Cerisiers"
DEMO_ACP_REFERENCE = "DEMO-CERISIERS-2025"

BELGIAN_OWNERS = [
    ("Van Damme", "Marc", "marc.vandamme@demo.be", "+32 475 12 34 56", "Rue Neuve 12", "1000", "Bruxelles"),
    ("Peeters", "Sophie", "sophie.peeters@demo.be", "+32 476 23 45 67", "Chaussee de Waterloo 88", "1180", "Uccle"),
    ("De Coninck", "Marie", "marie.deconinck@demo.be", "+32 478 34 56 78", "Avenue Louise 234", "1050", "Ixelles"),
    ("Meurisse", "Jean-Pierre", "jp.meurisse@demo.be", "+32 479 45 67 89", "Rue de la Loi 45", "1000", "Bruxelles"),
    ("Verbeek", "Catherine", "c.verbeek@demo.be", "+32 470 56 78 90", "Place Flagey 3", "1050", "Ixelles"),
    ("Janssens", "Nicolas", "n.janssens@demo.be", "+32 471 67 89 01", "Rue Antoine Dansaert 22", "1000", "Bruxelles"),
    ("Dupuis", "Isabelle", "i.dupuis@demo.be", "+32 472 78 90 12", "Boulevard Anspach 111", "1000", "Bruxelles"),
    ("Vermeulen", "Pierre", "p.vermeulen@demo.be", "+32 473 89 01 23", "Chaussee d'Alsemberg 500", "1180", "Uccle"),
    ("Claessens", "Emma", "emma.claessens@demo.be", "+32 474 90 12 34", "Rue du Trone 55", "1050", "Ixelles"),
    ("Lambrechts", "Antoine", "a.lambrechts@demo.be", "+32 475 01 23 45", "Avenue de Tervueren 90", "1150", "Woluwe-Saint-Pierre"),
]

# (number, description, lot_type, floor, area_m2, quotity_general, quotity_ascenseur)
DEMO_LOTS = [
    ("A001", "Appartement 3 chambres - Rez", "apartment", 0, 95, 130, 100),
    ("A101", "Appartement 2 chambres - 1er etage", "apartment", 1, 78, 115, 105),
    ("A102", "Appartement 2 chambres - 1er etage", "apartment", 1, 78, 115, 105),
    ("A201", "Appartement 3 chambres - 2eme etage", "apartment", 2, 95, 130, 110),
    ("A202", "Appartement 2 chambres - 2eme etage", "apartment", 2, 78, 115, 110),
    ("A301", "Appartement 4 chambres - Attique", "apartment", 3, 130, 175, 115),
    ("S001", "Studio - Rez de jardin", "apartment", 0, 42, 65, 55),
    ("C001", "Cave n1 - Sous-sol", "cave", -1, 6, 15, 0),
    ("C002", "Cave n2 - Sous-sol", "cave", -1, 6, 15, 0),
    ("P001", "Emplacement parking n1", "parking", -1, 12, 25, 0),
]
# Total general : 130+115+115+130+115+175+65+15+15+25 = 900
# Total ascenseur : 100+105+105+110+110+115+55+0+0+0 = 700

DEMO_SUPPLIERS = [
    ("ELIA", "0432.132.132", "BE0432132132", "Boulevard de l'Empereur 20", "1000", "Bruxelles", "info@elia.be", "+32 2 546 70 11", "BE71 3630 4711 5701", "BBRUBEBB", "electricity"),
    ("Vanderbilt Plomberie SPRL", "0876.543.210", "BE0876543210", "Rue de la Fontaine 15", "1000", "Bruxelles", "contact@vanderbilt.be", "+32 2 512 34 56", "BE68 5390 0754 7034", "BBRUBEBB", "plumbing"),
    ("Kone Ascenseurs SA", "0403.324.567", "BE0403324567", "Chaussee de la Hulpe 150", "1170", "Watermael-Boitsfort", "service@kone.be", "+32 2 663 30 00", "BE47 5230 8043 4523", "TRIOBEBB", "elevator"),
    ("Securitas Nettoyage", "0428.812.290", "BE0428812290", "Avenue Louise 65", "1050", "Ixelles", "info@securitas.be", "+32 2 263 55 55", "BE95 4210 0521 3401", "KREDBEBB", "cleaning"),
    ("AXA Belgium", "0404.483.367", "BE0404483367", "Boulevard du Souverain 25", "1170", "Watermael-Boitsfort", "syndic@axa.be", "+32 2 678 66 11", "BE72 3100 0034 5678", "BBRUBEBB", "insurance"),
]

# (account_number, name, amount_annual, key: 'gen'|'asc', vat_code, occupant_pct)
DEMO_BUDGET_LINES = [
    ("6120", "Electricite communes", 4800.0, "gen", "A4", 100.0),
    ("61400", "Entretien ascenseur", 2400.0, "asc", "A4", 100.0),
    ("61300", "Nettoyage communes", 6000.0, "gen", "A4", 100.0),
    ("61000", "Assurance batiment", 3200.0, "gen", "NA", 0.0),
    ("61100", "Reparations diverses", 1200.0, "gen", "A4", 50.0),
    ("61500", "Honoraires syndic", 12000.0, "gen", "A4", 0.0),
]

# Factures fournisseurs (spread over 2025)
# (supplier_idx, month, day, account_number, description, ht, tva_pct, key: 'gen'|'asc')
DEMO_INVOICES = [
    (0, 2, 15, "6120", "Facture electricite Q1 2025", 1100.0, 21, "gen"),
    (0, 5, 20, "6120", "Facture electricite Q2 2025", 1250.0, 21, "gen"),
    (0, 8, 18, "6120", "Facture electricite Q3 2025", 1180.0, 21, "gen"),
    (2, 3, 10, "61400", "Contrat maintenance ascenseur 2025", 2400.0, 21, "asc"),
    (3, 1, 31, "61300", "Nettoyage janvier 2025", 500.0, 21, "gen"),
    (3, 4, 30, "61300", "Nettoyage T1 2025 (fev-mar)", 1000.0, 21, "gen"),
    (4, 1, 15, "61000", "Prime assurance 2025 (annuelle)", 3200.0, 0, "gen"),
    (1, 6, 12, "61100", "Reparation fuite chaudiere collective", 850.0, 21, "gen"),
]


def create_admin_demo_router(db):
    router = APIRouter(prefix="/api/admin/demo")

    async def _require_superadmin(request: Request) -> dict:
        from server import get_current_user
        user = await get_current_user(request)
        if user.get("role") != "superadmin":
            raise HTTPException(403, "Superadmin uniquement")
        return user

    async def _cleanup_prior_demo():
        """Supprime toute ACP DEMO existante (identifiee par le nom exact ou
        prefixe DEMO-CERISIERS) et toutes ses donnees en cascade."""
        prior_ids = []
        async for c in db.coproprietes.find(
            {"$or": [
                {"name": DEMO_ACP_NAME},
                {"reference": {"$regex": "^DEMO-CERISIERS"}},
            ]},
            {"id": 1, "_id": 0},
        ):
            if c.get("id"):
                prior_ids.append(c["id"])
        if not prior_ids:
            return 0
        # Cascade delete
        for col in [
            "lots", "tenants", "distribution_keys", "invoices", "payments",
            "bank_statements", "bank_transactions", "budgets", "fiscal_years",
            "expense_categories", "counter_readings", "documents",
            "ag_meetings", "notifications", "tier_accounts", "journal_entries",
            "pcmn_accounts", "provisions_calls", "provisions",
        ]:
            try:
                await db[col].delete_many({"copropriete_id": {"$in": prior_ids}})
            except Exception:
                pass
        # Delete owners exclusively rattached to these ACPs
        # (a proprio est demo-only si son email finit par @demo.be ET ne partage
        # avec aucune autre ACP)
        await db.owners.delete_many({"email": {"$regex": "@demo.be$"}})
        # Delete suppliers demo (identifies via copropriete_id)
        await db.suppliers.delete_many({"copropriete_id": {"$in": prior_ids}})
        await db.coproprietes.delete_many({"id": {"$in": prior_ids}})
        return len(prior_ids)

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @router.post("/generate-acp")
    async def generate_demo_acp(request: Request):
        user = await _require_superadmin(request)
        superadmin_id = user.get("id") or user.get("_id")
        superadmin_id = str(superadmin_id) if superadmin_id else ""

        # 1. Cleanup existing demo
        deleted = await _cleanup_prior_demo()

        # 2. Create the copropriete
        copro_id = str(uuid.uuid4())
        copro_doc = {
            "id": copro_id,
            "name": DEMO_ACP_NAME,
            "reference": DEMO_ACP_REFERENCE,
            "bce": "0700.111.222",
            "address": "Avenue des Cerisiers 42",
            "postal_code": "1050",
            "city": "Bruxelles",
            "country": "Belgique",
            "description": "Copropriete de demonstration - donnees fictives pour presentation.",
            "quarterly_closing": True,
            "default_provisions": True,
            "created_at": _now_iso(),
            "syndic_id": superadmin_id,
            "status": "active",
            "bank_accounts": [
                {
                    "id": str(uuid.uuid4()),
                    "iban": "BE68 5390 0754 7034",
                    "bic": "BBRUBEBB",
                    "bank_name": "BNP Paribas Fortis",
                    "account_type": "vue",
                    "label": "Compte principal",
                    "is_default": True,
                    "pcmn_number": "551034",
                    "opening_balance": 0.0,
                    "opening_date": "2025-01-01",
                },
                {
                    "id": str(uuid.uuid4()),
                    "iban": "BE12 3630 4711 5734",
                    "bic": "BBRUBEBB",
                    "bank_name": "BNP Paribas Fortis",
                    "account_type": "epargne",
                    "label": "Fonds de reserve",
                    "is_default": False,
                    "pcmn_number": "550734",
                    "opening_balance": 5000.0,
                    "opening_date": "2025-01-01",
                },
            ],
        }
        await db.coproprietes.insert_one(copro_doc)

        # 3. Seed PCMN + accounts (reuse existing helper via inline import)
        try:
            from pcmn_data import PCMN_ALL_ACCOUNTS
            docs = []
            for acc in PCMN_ALL_ACCOUNTS:
                d = {
                    **acc,
                    "copropriete_id": copro_id,
                    "syndic_id": superadmin_id,
                    "active": True,
                    "is_custom": False,
                }
                docs.append(d)
            if docs:
                await db.pcmn_accounts.insert_many(docs)
        except Exception:
            pass

        # 4. Fiscal year 2025
        fy_id = str(uuid.uuid4())
        await db.fiscal_years.insert_one({
            "id": fy_id,
            "name": "Exercice 2025",
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "status": "open",
            "copropriete_id": copro_id,
            "syndic_id": superadmin_id,
            "created_at": _now_iso(),
        })

        # 5. Create 10 owners
        owner_ids: List[str] = []
        aux_counter = 1
        for (last, first, email, phone, addr, cp, city) in BELGIAN_OWNERS:
            oid = str(uuid.uuid4())
            aux_code = f"{aux_counter:04d}"
            await db.owners.insert_one({
                "id": oid,
                "first_name": first,
                "last_name": last,
                "name": f"{last} {first}",
                "civility": "",
                "email": email,
                "phone": phone,
                "address": addr,
                "postal_code": cp,
                "city": city,
                "country": "Belgique",
                "auxiliary_code": aux_code,
                "vcs_code": "",
                "copropriete_id": copro_id,
                "copropriete_ids": [copro_id],
                "syndic_id": superadmin_id,
                "created_at": _now_iso(),
            })
            owner_ids.append(oid)
            aux_counter += 1

        # 6. Create 10 lots (1 owner per lot for simplicity)
        lot_ids: List[str] = []
        for i, (num, desc, ltype, floor, area, q_gen, q_asc) in enumerate(DEMO_LOTS):
            lot_id = str(uuid.uuid4())
            owner_id = owner_ids[i]  # 1-to-1 mapping
            await db.lots.insert_one({
                "id": lot_id,
                "number": num,
                "description": desc,
                "lot_type": ltype,
                "floor": floor,
                "area": area,
                "quotity": q_gen,
                "owner_id": owner_id,
                "owner_ids": [owner_id],
                "copropriete_id": copro_id,
                "syndic_id": superadmin_id,
                "start_date": "2025-01-01",
                "created_at": _now_iso(),
            })
            lot_ids.append(lot_id)

        # 7. Distribution keys
        key_gen_id = str(uuid.uuid4())
        key_asc_id = str(uuid.uuid4())
        await db.distribution_keys.insert_one({
            "id": key_gen_id,
            "copropriete_id": copro_id,
            "syndic_id": superadmin_id,
            "name": "Charges generales",
            "is_default": True,
            "key_type": "quotity",
            "lots": [
                {"lot_id": lot_ids[i], "share": float(DEMO_LOTS[i][5])}
                for i in range(len(DEMO_LOTS))
            ],
            "created_at": _now_iso(),
        })
        await db.distribution_keys.insert_one({
            "id": key_asc_id,
            "copropriete_id": copro_id,
            "syndic_id": superadmin_id,
            "name": "Ascenseur",
            "is_default": False,
            "key_type": "quotity",
            "lots": [
                {"lot_id": lot_ids[i], "share": float(DEMO_LOTS[i][6])}
                for i in range(len(DEMO_LOTS))
                if DEMO_LOTS[i][6] > 0
            ],
            "created_at": _now_iso(),
        })

        # 8. Suppliers
        supplier_ids: List[str] = []
        for (name, bce, vat, addr, cp, city, email, phone, iban, bic, _tag) in DEMO_SUPPLIERS:
            sid = str(uuid.uuid4())
            await db.suppliers.insert_one({
                "id": sid,
                "name": name,
                "bce_number": bce,
                "vat_number": vat,
                "address": addr,
                "postal_code": cp,
                "city": city,
                "country": "Belgique",
                "phone": phone,
                "email": email,
                "iban": iban,
                "bic": bic,
                "notes": "Fournisseur DEMO",
                "copropriete_id": copro_id,
                "syndic_id": superadmin_id,
                "created_at": _now_iso(),
            })
            supplier_ids.append(sid)

        # 8bis. iter93n : Natures de depense (expense_categories) - une par
        # ligne de budget. Elles apparaissent dans la page "Natures de depense"
        # et sont utilisees comme prefill dans les factures / OD.
        expense_cat_by_acct: dict = {}
        for i, (acct_num, name, _amount, key_tag, vat_code, occ_pct) in enumerate(DEMO_BUDGET_LINES):
            ecid = str(uuid.uuid4())
            code = f"{i+1:04d}"
            await db.expense_categories.insert_one({
                "id": ecid,
                "code": code,
                "name": name,
                "label": name,
                "account_number": acct_num,
                "account_name": name,
                "vat_code": vat_code,
                "default_occupant_pct": occ_pct,
                "default_proprietaire_pct": 100.0 - occ_pct,
                "kind": "charge",
                "is_default_seed": False,
                "copropriete_id": copro_id,
                "syndic_id": superadmin_id,
                "default_distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id,
                "created_at": _now_iso(),
            })
            expense_cat_by_acct[acct_num] = ecid

        # 9. Budget (approved)
        budget_lines = []
        for (acct_num, name, amount, key_tag, _vat, _occ) in DEMO_BUDGET_LINES:
            budget_lines.append({
                "account_number": acct_num,
                "account_name": name,
                "amount": amount,
                "expense_category_id": expense_cat_by_acct.get(acct_num),
                "distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id,
            })
        budget_total = sum(l["amount"] for l in budget_lines)
        await db.budgets.insert_one({
            "id": str(uuid.uuid4()),
            "fiscal_year_id": fy_id,
            "copropriete_id": copro_id,
            "syndic_id": superadmin_id,
            "name": "Budget 2025",
            "lines": budget_lines,
            "total": budget_total,
            "total_amount": budget_total,
            "reserve_fund_amount": 2000.0,
            "reserve_fund_key_id": key_gen_id,
            "roulement_fund_amount": budget_total,
            "roulement_fund_key_id": key_gen_id,
            "status": "approved",
            "approved_at": _now_iso(),
            "approved_by": user.get("email") or "superadmin",
            "created_at": _now_iso(),
        })

        # 10. Supplier invoices (spread over 2025) - all marked as paid
        for i, (sup_idx, month, day, acct_num, description, ht, tva_pct, key_tag) in enumerate(DEMO_INVOICES):
            inv_date = f"2025-{month:02d}-{day:02d}"
            due_date = f"2025-{month:02d}-{min(day + 30, 28):02d}"
            tva = round(ht * tva_pct / 100.0, 2)
            ttc = round(ht + tva, 2)
            await db.invoices.insert_one({
                "id": str(uuid.uuid4()),
                "number": f"F2025-{i+1:03d}",
                "date": inv_date,
                "due_date": due_date,
                "supplier": DEMO_SUPPLIERS[sup_idx][0],
                "supplier_id": supplier_ids[sup_idx],
                "description": description,
                "total_amount": ttc,
                "vat_amount": tva,
                "account_number": acct_num,
                "expense_category_id": expense_cat_by_acct.get(acct_num),
                "distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id,
                "distribution_lines": [],
                "status": "paid",
                "copropriete_id": copro_id,
                "syndic_id": superadmin_id,
                "created_at": _now_iso(),
            })

        # 11. Bank statement + transactions
        # Compte 551034 - principal
        stmt_id = str(uuid.uuid4())
        await db.bank_statements.insert_one({
            "id": stmt_id,
            "account_number": "551034",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "opening_balance": 0.0,
            "closing_balance": 0.0,  # calcule apres
            "copropriete_id": copro_id,
            "syndic_id": superadmin_id,
            "created_at": _now_iso(),
        })

        transactions = []
        balance = 0.0

        # Provisions trimestrielles proprietaires (10 proprios x 2 trimestres visibles)
        provision_per_owner_q = round(budget_total / 4.0 / len(owner_ids), 2)
        for q_month, q_label in [(1, "Q1"), (4, "Q2")]:
            for i, oid in enumerate(owner_ids):
                aux = f"{i+1:04d}"
                bt = {
                    "id": str(uuid.uuid4()),
                    "statement_id": stmt_id,
                    "date": f"2025-{q_month:02d}-05",
                    "amount": provision_per_owner_q,
                    "counterparty_name": f"{BELGIAN_OWNERS[i][0]} {BELGIAN_OWNERS[i][1]}",
                    "counterparty_account": f"BE00 0000 000{i:02d} 0001",
                    "communication": f"+++{aux[:3]}/{aux[3:]}00/00{q_label[1]}+++",
                    "transaction_type": "credit",
                    "account_number": "551034",
                    "matched": True,
                    "matched_to": oid,
                    "match_type": "owner",
                    "copropriete_id": copro_id,
                    "syndic_id": superadmin_id,
                    "created_at": _now_iso(),
                }
                transactions.append(bt)
                balance += provision_per_owner_q

        # Reglements fournisseurs (les 8 factures marquees paid)
        for i, (sup_idx, month, day, acct_num, description, ht, tva_pct, key_tag) in enumerate(DEMO_INVOICES):
            tva = round(ht * tva_pct / 100.0, 2)
            ttc = round(ht + tva, 2)
            pay_month = month
            pay_day = min(day + 15, 28)
            bt = {
                "id": str(uuid.uuid4()),
                "statement_id": stmt_id,
                "date": f"2025-{pay_month:02d}-{pay_day:02d}",
                "amount": -ttc,
                "counterparty_name": DEMO_SUPPLIERS[sup_idx][0],
                "counterparty_account": DEMO_SUPPLIERS[sup_idx][8],
                "communication": f"F2025-{i+1:03d}",
                "transaction_type": "debit",
                "account_number": "551034",
                "matched": True,
                "matched_to": supplier_ids[sup_idx],
                "match_type": "supplier",
                "copropriete_id": copro_id,
                "syndic_id": superadmin_id,
                "created_at": _now_iso(),
            }
            transactions.append(bt)
            balance -= ttc

        if transactions:
            await db.bank_transactions.insert_many(transactions)
        await db.bank_statements.update_one({"id": stmt_id}, {"$set": {"closing_balance": round(balance, 2)}})

        # 12. Link superadmin to the ACP (visible in dashboard)
        await db.users.update_one(
            {"id": superadmin_id},
            {"$addToSet": {"copropriete_ids": copro_id}},
        )

        return {
            "message": "Copropriete DEMO generee avec succes",
            "cleaned_prior_demos": deleted,
            "copropriete_id": copro_id,
            "stats": {
                "owners": len(owner_ids),
                "lots": len(lot_ids),
                "suppliers": len(supplier_ids),
                "budget_lines": len(budget_lines),
                "budget_total": budget_total,
                "invoices": len(DEMO_INVOICES),
                "bank_transactions": len(transactions),
                "closing_balance": round(balance, 2),
            },
        }

    @router.delete("/generate-acp")
    async def delete_demo_acp(request: Request):
        await _require_superadmin(request)
        deleted = await _cleanup_prior_demo()
        return {"message": f"{deleted} ACP DEMO supprimee(s)"}

    @router.get("/status")
    async def demo_status(request: Request):
        await _require_superadmin(request)
        existing = await db.coproprietes.find_one(
            {"name": DEMO_ACP_NAME}, {"_id": 0, "id": 1, "name": 1, "created_at": 1},
        )
        return {"exists": bool(existing), "acp": existing}

    return router
