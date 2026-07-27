"""iter93o : Generateur de copropriete DEMO comptablement complet.
Superadmin ONLY.

Structure comptable belge (PCMN) :
- 10 lots totalisant 10.000 milliemes (cle generale)
- 10 proprietaires avec comptes tiers 4101xxxx (fonds roulement) et 4100xxxx (reserve)
- Journal AN : ouverture avec fonds de reserve 5.000 EUR
- Journal VE : 4 appels trimestriels (budget 12.000 EUR / 4 = 3.000 EUR / trimestre)
- Journal AC : 5 factures fournisseurs (elec, ascenseur, syndic, nettoyage, assurance)
- Journal FI : 2 paiements bancaires recus (pour tester le lettrage)
- Bilan et Balance des tiers coherents (equilibre debits = credits).
"""
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Request

DEMO_ACP_NAME = "DEMO - Residence Les Cerisiers"
DEMO_ACP_REFERENCE = "DEMO-CERISIERS-2025"

# (last, first, email, phone, addr, cp, city)
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
# Somme quotity_general = 10 000 milliemes exactement
DEMO_LOTS = [
    ("A001", "Appartement 3 chambres - Rez",           "apartment",  0,  95, 1400,  900),
    ("A101", "Appartement 2 chambres - 1er etage",      "apartment",  1,  78, 1100,  900),
    ("A102", "Appartement 2 chambres - 1er etage",      "apartment",  1,  78, 1100,  900),
    ("A201", "Appartement 3 chambres - 2eme etage",     "apartment",  2,  95, 1400,  950),
    ("A202", "Appartement 2 chambres - 2eme etage",     "apartment",  2,  78, 1100,  950),
    ("A301", "Appartement 4 chambres - Attique",        "apartment",  3, 130, 1900, 1200),
    ("S001", "Studio - Rez de jardin",                  "apartment",  0,  42,  700,  600),
    ("C001", "Cave n1 - Sous-sol",                      "cave",      -1,   6,  150,    0),
    ("C002", "Cave n2 - Sous-sol",                      "cave",      -1,   6,  150,    0),
    ("P001", "Emplacement parking n1",                  "parking",   -1,  12, 1000,    0),
]
assert sum(l[5] for l in DEMO_LOTS) == 10000, "Quotites generales doivent totaliser 10000 milliemes"

# 5 fournisseurs (l'utilisateur a demande 5 factures : electricite, ascenseur, syndic + 2 autres)
DEMO_SUPPLIERS = [
    ("ELIA",                       "0432.132.132", "BE0432132132", "Boulevard de l'Empereur 20", "1000", "Bruxelles",                "info@elia.be",     "+32 2 546 70 11", "BE71 3630 4711 5701", "BBRUBEBB"),
    ("Kone Ascenseurs SA",         "0403.324.567", "BE0403324567", "Chaussee de la Hulpe 150",   "1170", "Watermael-Boitsfort",      "service@kone.be",  "+32 2 663 30 00", "BE47 5230 8043 4523", "TRIOBEBB"),
    ("NextGeCopro (Syndic)",       "0700.111.222", "BE0700111222", "Rue du Syndic 1",            "1000", "Bruxelles",                "info@nextgecopro.be", "+32 2 555 00 11", "BE68 5390 0754 7034", "BBRUBEBB"),
    ("Securitas Nettoyage",        "0428.812.290", "BE0428812290", "Avenue Louise 65",           "1050", "Ixelles",                  "info@securitas.be","+32 2 263 55 55", "BE95 4210 0521 3401", "KREDBEBB"),
    ("AXA Belgium",                "0404.483.367", "BE0404483367", "Boulevard du Souverain 25", "1170", "Watermael-Boitsfort",      "syndic@axa.be",    "+32 2 678 66 11", "BE72 3100 0034 5678", "BBRUBEBB"),
]

# 6 natures de depense couvrant le budget annuel 12.000 EUR
# (account_number, name, amount_annual, key: 'gen'|'asc', vat_code, occupant_pct)
DEMO_BUDGET_LINES = [
    ("6120",  "Electricite communes",  2400.0, "gen", "A4", 100.0),
    ("61400", "Entretien ascenseur",   1800.0, "asc", "A4", 100.0),
    ("61300", "Nettoyage communes",    3600.0, "gen", "A4", 100.0),
    ("61000", "Assurance batiment",    1800.0, "gen", "NA",   0.0),
    ("61100", "Reparations diverses",   600.0, "gen", "A4",  50.0),
    ("61500", "Honoraires syndic",     1800.0, "gen", "A4",   0.0),
]
assert sum(l[2] for l in DEMO_BUDGET_LINES) == 12000, "Budget annuel doit totaliser 12000 EUR"

# 5 factures fournisseurs sur 2025
# (supplier_idx, month, day, account_number, description, ht, tva_pct, key: 'gen'|'asc')
DEMO_INVOICES = [
    (0, 2, 15, "6120",  "Facture electricite Q1 2025",       600.0,  21, "gen"),  # ELIA
    (1, 3, 10, "61400", "Contrat maintenance ascenseur 2025", 1800.0, 21, "asc"),  # Kone (couvre l'annee)
    (2, 4, 30, "61500", "Honoraires syndic Q1+Q2 2025",       900.0, 21, "gen"),  # NextGeCopro
    (3, 5, 20, "61300", "Nettoyage T1+T2 2025",              1200.0, 21, "gen"),  # Securitas
    (4, 1, 15, "61000", "Prime assurance 2025 (annuelle)",   1800.0,  0, "gen"),  # AXA
]

# 2 paiements bancaires recus (pour tester le lettrage) - viennent de proprios 1 et 3
DEMO_PAYMENTS_RECEIVED = [
    # (owner_idx, month, day, quarter_number_1based, amount_override_or_None)
    (0, 1, 12, 1, None),  # Van Damme paie son Q1 tot
    (2, 1, 15, 1, None),  # De Coninck paie son Q1 tot
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
        """Supprime toute ACP DEMO existante et toutes ses donnees en cascade."""
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
        await db.owners.delete_many({"email": {"$regex": "@demo.be$"}})
        await db.suppliers.delete_many({"copropriete_id": {"$in": prior_ids}})
        await db.coproprietes.delete_many({"id": {"$in": prior_ids}})
        return len(prior_ids)

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _owner_provision(quotity_share: int, budget_total: float, per_quarter: bool = True) -> float:
        """Calcule la quote-part proprio pour un appel trimestriel."""
        base = budget_total / 4.0 if per_quarter else budget_total
        return round(base * quotity_share / 10000.0, 2)

    @router.post("/generate-acp")
    async def generate_demo_acp(request: Request):
        user = await _require_superadmin(request)
        superadmin_id = user.get("id") or user.get("_id")
        superadmin_id = str(superadmin_id) if superadmin_id else ""

        # ============================================================
        # 1. Cleanup + creation copropriete + PCMN + FY
        # ============================================================
        deleted = await _cleanup_prior_demo()
        copro_id = str(uuid.uuid4())
        BUDGET_TOTAL = 12000.0
        RESERVE_INITIAL = 5000.0
        BANK_ROULEMENT = "551034"   # Banque compte courant
        BANK_RESERVE   = "550734"   # Banque fonds de reserve

        copro_doc = {
            "id": copro_id,
            "name": DEMO_ACP_NAME,
            "reference": DEMO_ACP_REFERENCE,
            "bce": "0700.111.222",
            "address": "Avenue des Cerisiers 42",
            "postal_code": "1050",
            "city": "Bruxelles",
            "country": "Belgique",
            "description": "Copropriete de demonstration - donnees fictives coherentes.",
            "quarterly_closing": True,
            "default_provisions": True,
            "created_at": _now_iso(),
            "syndic_id": superadmin_id,
            "status": "active",
            "bank_accounts": [
                {"id": str(uuid.uuid4()), "iban": "BE68 5390 0754 7034", "bic": "BBRUBEBB",
                 "bank_name": "BNP Paribas Fortis", "account_type": "vue", "label": "Compte principal",
                 "is_default": True, "pcmn_number": BANK_ROULEMENT,
                 "opening_balance": 0.0, "opening_date": "2025-01-01"},
                {"id": str(uuid.uuid4()), "iban": "BE12 3630 4711 5734", "bic": "BBRUBEBB",
                 "bank_name": "BNP Paribas Fortis", "account_type": "epargne", "label": "Fonds de reserve",
                 "is_default": False, "pcmn_number": BANK_RESERVE,
                 "opening_balance": RESERVE_INITIAL, "opening_date": "2025-01-01"},
            ],
        }
        await db.coproprietes.insert_one(copro_doc)

        # PCMN de base
        try:
            from pcmn_data import PCMN_ALL_ACCOUNTS
            docs = []
            for acc in PCMN_ALL_ACCOUNTS:
                docs.append({**acc, "copropriete_id": copro_id, "syndic_id": superadmin_id, "active": True, "is_custom": False})
            if docs:
                await db.pcmn_accounts.insert_many(docs)
        except Exception:
            pass

        # Fiscal year 2025
        fy_id = str(uuid.uuid4())
        await db.fiscal_years.insert_one({
            "id": fy_id, "name": "Exercice 2025",
            "start_date": "2025-01-01", "end_date": "2025-12-31",
            "status": "open", "copropriete_id": copro_id, "syndic_id": superadmin_id,
            "created_at": _now_iso(),
        })

        # ============================================================
        # 2. Owners (10) + comptes tiers 4101xxxx / 4100xxxx
        # ============================================================
        owner_ids: List[str] = []
        owner_names: List[str] = []
        owner_roulement_acct: List[str] = []  # 4101xxxx per owner
        owner_reserve_acct: List[str] = []    # 4100xxxx per owner
        tier_pcmn_docs = []
        for i, (last, first, email, phone, addr, cp, city) in enumerate(BELGIAN_OWNERS):
            oid = str(uuid.uuid4())
            aux_code = f"{i+1:04d}"
            display_name = f"{last} {first}"
            roul_num = f"4101{aux_code}"   # 41010001 -> 41010010
            res_num = f"4100{aux_code}"    # 41000001 -> 41000010
            # iter93o : `tier_accounts[copro_id]` liste les comptes tiers du proprio
            # pour cette ACP -> utilise par balance-tiers et bilan pour agreger.
            await db.owners.insert_one({
                "id": oid,
                "first_name": first, "last_name": last, "name": display_name, "civility": "",
                "email": email, "phone": phone, "address": addr, "postal_code": cp, "city": city, "country": "Belgique",
                "auxiliary_code": aux_code, "vcs_code": aux_code,
                "copropriete_id": copro_id, "copropriete_ids": [copro_id],
                "tier_accounts": {copro_id: {"provisions": roul_num, "reserve": res_num}},
                "syndic_id": superadmin_id, "created_at": _now_iso(),
            })
            owner_ids.append(oid)
            owner_names.append(display_name)
            owner_roulement_acct.append(roul_num)
            owner_reserve_acct.append(res_num)
            tier_pcmn_docs.append({
                "number": roul_num, "name": f"Acompte de fonds de roulement appele - {display_name}",
                "class_num": 4, "type": "actif", "copropriete_id": copro_id, "syndic_id": superadmin_id,
                "active": True, "is_custom": False, "owner_id": oid,
            })
            tier_pcmn_docs.append({
                "number": res_num, "name": f"Acompte de fonds de reserve appele - {display_name}",
                "class_num": 4, "type": "actif", "copropriete_id": copro_id, "syndic_id": superadmin_id,
                "active": True, "is_custom": False, "owner_id": oid,
            })
        if tier_pcmn_docs:
            await db.pcmn_accounts.insert_many(tier_pcmn_docs)

        # ============================================================
        # 3. Lots (10) + Distribution keys (2)
        # ============================================================
        lot_ids: List[str] = []
        for i, (num, desc, ltype, floor, area, q_gen, q_asc) in enumerate(DEMO_LOTS):
            lot_id = str(uuid.uuid4())
            oid = owner_ids[i]
            await db.lots.insert_one({
                "id": lot_id, "number": num, "description": desc, "lot_type": ltype,
                "floor": floor, "area": area, "quotity": q_gen,
                "owner_id": oid, "owner_ids": [oid],
                "copropriete_id": copro_id, "syndic_id": superadmin_id,
                "start_date": "2025-01-01", "created_at": _now_iso(),
            })
            lot_ids.append(lot_id)

        key_gen_id = str(uuid.uuid4())
        key_asc_id = str(uuid.uuid4())
        await db.distribution_keys.insert_one({
            "id": key_gen_id, "copropriete_id": copro_id, "syndic_id": superadmin_id,
            "name": "Charges generales", "is_default": True, "key_type": "quotity",
            "lots": [{"lot_id": lot_ids[i], "share": float(DEMO_LOTS[i][5])} for i in range(len(DEMO_LOTS))],
            "created_at": _now_iso(),
        })
        await db.distribution_keys.insert_one({
            "id": key_asc_id, "copropriete_id": copro_id, "syndic_id": superadmin_id,
            "name": "Ascenseur", "is_default": False, "key_type": "quotity",
            "lots": [{"lot_id": lot_ids[i], "share": float(DEMO_LOTS[i][6])} for i in range(len(DEMO_LOTS)) if DEMO_LOTS[i][6] > 0],
            "created_at": _now_iso(),
        })

        # ============================================================
        # 4. Suppliers + Expense categories (natures)
        # ============================================================
        supplier_ids: List[str] = []
        for (name, bce, vat, addr, cp, city, email, phone, iban, bic) in DEMO_SUPPLIERS:
            sid = str(uuid.uuid4())
            await db.suppliers.insert_one({
                "id": sid, "name": name, "bce_number": bce, "vat_number": vat,
                "address": addr, "postal_code": cp, "city": city, "country": "Belgique",
                "phone": phone, "email": email, "iban": iban, "bic": bic,
                "notes": "Fournisseur DEMO", "copropriete_id": copro_id, "syndic_id": superadmin_id,
                "created_at": _now_iso(),
            })
            supplier_ids.append(sid)

        expense_cat_by_acct: dict = {}
        for i, (acct_num, name, _amount, key_tag, vat_code, occ_pct) in enumerate(DEMO_BUDGET_LINES):
            ecid = str(uuid.uuid4())
            await db.expense_categories.insert_one({
                "id": ecid, "code": f"{i+1:04d}", "name": name, "label": name,
                "account_number": acct_num, "account_name": name, "vat_code": vat_code,
                "default_occupant_pct": occ_pct, "default_proprietaire_pct": 100.0 - occ_pct,
                "kind": "charge", "is_default_seed": False,
                "copropriete_id": copro_id, "syndic_id": superadmin_id,
                "default_distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id,
                "created_at": _now_iso(),
            })
            expense_cat_by_acct[acct_num] = ecid

        # ============================================================
        # 5. Budget approuve
        # ============================================================
        budget_lines = [
            {"account_number": a, "account_name": n, "amount": amt,
             "expense_category_id": expense_cat_by_acct.get(a),
             "distribution_key_id": key_asc_id if k == "asc" else key_gen_id}
            for (a, n, amt, k, _v, _o) in DEMO_BUDGET_LINES
        ]
        await db.budgets.insert_one({
            "id": str(uuid.uuid4()), "fiscal_year_id": fy_id,
            "copropriete_id": copro_id, "syndic_id": superadmin_id,
            "name": "Budget 2025", "lines": budget_lines,
            "total": BUDGET_TOTAL, "total_amount": BUDGET_TOTAL,
            "reserve_fund_amount": RESERVE_INITIAL, "reserve_fund_key_id": key_gen_id,
            "roulement_fund_amount": BUDGET_TOTAL, "roulement_fund_key_id": key_gen_id,
            "status": "approved", "approved_at": _now_iso(),
            "approved_by": user.get("email") or "superadmin", "created_at": _now_iso(),
        })

        # ============================================================
        # 6. JOURNAUX COMPTABLES
        # ============================================================
        journal_entries = []

        def _entry(journal_type: str, date: str, ref: str, description: str, lines: list, tier_id: str = None) -> dict:
            total_d = round(sum(l["debit"] for l in lines), 2)
            total_c = round(sum(l["credit"] for l in lines), 2)
            return {
                "id": str(uuid.uuid4()),
                "journal_type": journal_type, "date": date, "reference": ref,
                "description": description, "lines": lines,
                "total_debit": total_d, "total_credit": total_c,
                "fiscal_year_id": fy_id, "copropriete_id": copro_id,
                "syndic_id": superadmin_id, "status": "posted",
                "created_at": _now_iso(),
            }

        # --- Journal AN : Ouverture (fonds de reserve 5000 EUR) ---
        an_entry = _entry(
            "AN", "2025-01-01", "AN-2025-001",
            "Ouverture exercice - Fonds de reserve report",
            [
                {"account_number": BANK_RESERVE, "account_name": "Banque - Fonds de reserve",
                 "debit": RESERVE_INITIAL, "credit": 0.0},
                {"account_number": "100000", "account_name": "Fonds de reserve",
                 "debit": 0.0, "credit": RESERVE_INITIAL},
            ],
        )
        an_entry["is_opening_balance"] = True  # obligatoire pour balance-tiers/bilan
        journal_entries.append(an_entry)

        # --- Journal VE : 4 appels trimestriels (3000 EUR / trimestre) ---
        quarterly_amount = BUDGET_TOTAL / 4.0  # 3000 EUR
        for q_idx, (q_month, q_day) in enumerate([(1, 5), (4, 5), (7, 5), (10, 5)], start=1):
            date_call = f"2025-{q_month:02d}-{q_day:02d}"
            lines = []
            # Une ligne debit par proprietaire (4101xxxx)
            distributed_total = 0.0
            for i, oid in enumerate(owner_ids):
                q_gen = DEMO_LOTS[i][5]  # quotites generales
                share = _owner_provision(q_gen, BUDGET_TOTAL, per_quarter=True)
                distributed_total += share
                lines.append({
                    "account_number": owner_roulement_acct[i],
                    "account_name": f"Fonds roulement - {owner_names[i]}",
                    "debit": share, "credit": 0.0,
                    "third_party_id": oid,
                    "distribution_key_id": key_gen_id,
                })
            # Ajustement d'arrondi si necessaire (ecart <= 0.10)
            diff = round(quarterly_amount - distributed_total, 2)
            if abs(diff) > 0.001:
                lines[0]["debit"] = round(lines[0]["debit"] + diff, 2)
                distributed_total = round(distributed_total + diff, 2)
            # Credit unique : compte produit 730 (fonds roulement appele)
            lines.append({
                "account_number": "730000",
                "account_name": "Fonds de roulement appele",
                "debit": 0.0, "credit": round(distributed_total, 2),
                "distribution_key_id": key_gen_id,
            })
            journal_entries.append(_entry(
                "VE", date_call, f"VE-2025-Q{q_idx:03d}",
                f"Appel de fonds Q{q_idx} 2025 (10 proprietaires)",
                lines,
            ))

        # --- Journal AC : 5 factures fournisseurs ---
        invoice_docs = []
        for i, (sup_idx, month, day, acct_num, description, ht, tva_pct, key_tag) in enumerate(DEMO_INVOICES):
            inv_date = f"2025-{month:02d}-{day:02d}"
            due_date = f"2025-{month:02d}-{min(day + 30, 28):02d}"
            tva = round(ht * tva_pct / 100.0, 2)
            ttc = round(ht + tva, 2)
            inv_id = str(uuid.uuid4())
            invoice_docs.append({
                "id": inv_id, "number": f"F2025-{i+1:03d}", "date": inv_date, "due_date": due_date,
                "supplier": DEMO_SUPPLIERS[sup_idx][0], "supplier_id": supplier_ids[sup_idx],
                "description": description, "total_amount": ttc, "vat_amount": tva,
                "account_number": acct_num, "expense_category_id": expense_cat_by_acct.get(acct_num),
                "distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id,
                "distribution_lines": [], "status": "unpaid",
                "copropriete_id": copro_id, "syndic_id": superadmin_id, "created_at": _now_iso(),
            })
            # Ecriture AC : charge + TVA -> fournisseur
            supplier_acct = f"4400{sup_idx+1:04d}"  # 44000001, 44000002...
            ac_lines = [
                {"account_number": acct_num, "account_name": DEMO_BUDGET_LINES[[b[0] for b in DEMO_BUDGET_LINES].index(acct_num)][1] if acct_num in [b[0] for b in DEMO_BUDGET_LINES] else description,
                 "debit": ht, "credit": 0.0,
                 "expense_category_id": expense_cat_by_acct.get(acct_num),
                 "distribution_key_id": key_asc_id if key_tag == "asc" else key_gen_id},
            ]
            if tva > 0:
                ac_lines.append({"account_number": "411000", "account_name": "TVA a recuperer",
                                 "debit": tva, "credit": 0.0})
            ac_lines.append({"account_number": supplier_acct,
                             "account_name": DEMO_SUPPLIERS[sup_idx][0],
                             "debit": 0.0, "credit": ttc,
                             "third_party_id": supplier_ids[sup_idx]})
            journal_entries.append(_entry(
                "AC", inv_date, f"AC-2025-{i+1:03d}",
                f"Facture {DEMO_SUPPLIERS[sup_idx][0]} - {description}",
                ac_lines,
            ))
            # Aussi creer le compte fournisseur specifique dans pcmn_accounts
            await db.pcmn_accounts.update_one(
                {"number": supplier_acct, "copropriete_id": copro_id},
                {"$setOnInsert": {
                    "number": supplier_acct, "name": DEMO_SUPPLIERS[sup_idx][0],
                    "class_num": 4, "type": "passif", "copropriete_id": copro_id,
                    "syndic_id": superadmin_id, "active": True, "is_custom": False,
                    "supplier_id": supplier_ids[sup_idx],
                }},
                upsert=True,
            )
        if invoice_docs:
            await db.invoices.insert_many(invoice_docs)

        # --- Journal FI : 2 paiements bancaires recus des proprios ---
        bank_transactions = []
        stmt_id = str(uuid.uuid4())
        await db.bank_statements.insert_one({
            "id": stmt_id, "account_number": BANK_ROULEMENT,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
            "opening_balance": 0.0, "closing_balance": 0.0,
            "copropriete_id": copro_id, "syndic_id": superadmin_id, "created_at": _now_iso(),
        })
        balance_roulement = 0.0
        for (owner_idx, month, day, quarter, _amt) in DEMO_PAYMENTS_RECEIVED:
            q_gen = DEMO_LOTS[owner_idx][5]
            amount = _owner_provision(q_gen, BUDGET_TOTAL, per_quarter=True)
            date_pay = f"2025-{month:02d}-{day:02d}"
            oid = owner_ids[owner_idx]
            aux = f"{owner_idx+1:04d}"
            # Ecriture FI (paiement recu)
            fi_lines = [
                {"account_number": BANK_ROULEMENT, "account_name": "Banque compte courant",
                 "debit": amount, "credit": 0.0},
                {"account_number": owner_roulement_acct[owner_idx],
                 "account_name": f"Fonds roulement - {owner_names[owner_idx]}",
                 "debit": 0.0, "credit": amount, "third_party_id": oid},
            ]
            journal_entries.append(_entry(
                "FI", date_pay, f"FI-2025-{owner_idx+1:03d}",
                f"Paiement Q{quarter} recu de {owner_names[owner_idx]}",
                fi_lines,
            ))
            # Transaction bancaire correspondante (pour lettrage)
            bank_transactions.append({
                "id": str(uuid.uuid4()), "statement_id": stmt_id,
                "date": date_pay, "amount": amount,
                "counterparty_name": owner_names[owner_idx],
                "counterparty_account": f"BE00 0000 000{owner_idx:02d} 0001",
                "communication": f"+++{aux[:3]}/{aux[3:]}00/00{quarter}+++",
                "transaction_type": "credit", "account_number": BANK_ROULEMENT,
                "matched": True, "matched_to": oid, "match_type": "owner",
                "copropriete_id": copro_id, "syndic_id": superadmin_id, "created_at": _now_iso(),
            })
            balance_roulement += amount

        if journal_entries:
            await db.journal_entries.insert_many(journal_entries)
        if bank_transactions:
            await db.bank_transactions.insert_many(bank_transactions)
        await db.bank_statements.update_one({"id": stmt_id}, {"$set": {"closing_balance": round(balance_roulement, 2)}})

        # Link superadmin
        await db.users.update_one(
            {"id": superadmin_id},
            {"$addToSet": {"copropriete_ids": copro_id}},
        )

        # Verification de coherence : somme des debits = somme des credits (tous journaux)
        total_debit = round(sum(je["total_debit"] for je in journal_entries), 2)
        total_credit = round(sum(je["total_credit"] for je in journal_entries), 2)
        return {
            "message": "Copropriete DEMO generee - comptablement complete",
            "cleaned_prior_demos": deleted,
            "copropriete_id": copro_id,
            "stats": {
                "owners": len(owner_ids),
                "lots": len(lot_ids),
                "suppliers": len(supplier_ids),
                "expense_categories": 6,
                "budget_lines": 6,
                "budget_total": BUDGET_TOTAL,
                "invoices": len(invoice_docs),
                "journal_entries": len(journal_entries),
                "AN_count": sum(1 for j in journal_entries if j["journal_type"] == "AN"),
                "VE_count": sum(1 for j in journal_entries if j["journal_type"] == "VE"),
                "AC_count": sum(1 for j in journal_entries if j["journal_type"] == "AC"),
                "FI_count": sum(1 for j in journal_entries if j["journal_type"] == "FI"),
                "total_debit": total_debit,
                "total_credit": total_credit,
                "balanced": abs(total_debit - total_credit) < 0.01,
                "bank_transactions": len(bank_transactions),
                "reserve_initial": RESERVE_INITIAL,
                "quotities_total": sum(l[5] for l in DEMO_LOTS),
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
