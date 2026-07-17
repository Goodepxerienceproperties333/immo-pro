"""Owner portal: routes scopees automatiquement au proprietaire connecte.
Le lien user<->owner se fait par email (les owners sont globaux).
Toutes les donnees sont en lecture seule (RBAC enforce write-block sur role=owner).

iter89 : ajout de PUT /me (modif coords par le proprio) et CRUD locataires
self-service depuis le portail, avec notification automatique au syndic.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
import uuid
from datetime import datetime, timezone
from owner_self_notify import notify_syndic_of_owner_change


# Champs qu'un proprio est AUTORISE a modifier via le portail
# (les autres - vcs_code, auxiliary_code, name, tier_accounts - sont locked
# car ils ont un impact comptable et identitaire).
OWNER_SELF_EDITABLE = {
    "first_name", "last_name", "address", "postal_code", "city",
    "country", "email", "email2", "phone", "phone2",
}


async def _resolve_owner(db, request: Request) -> dict:
    """Find the owner record matching the current authenticated user (by email).

    Retourne LE PREMIER owner match (identite principale). Pour les endpoints
    qui doivent inclure les fiches multi-ACP (un meme email peut avoir des
    fiches distinctes dans plusieurs ACPs), utiliser `_resolve_owner_ids` a la
    place.
    """
    email = getattr(request.state, "user_email", "") or ""
    if not email:
        raise HTTPException(401, "Not authenticated")
    owner = await db.owners.find_one({"email": email.lower().strip()}, {"_id": 0})
    if not owner:
        # Fallback: try email2 too
        owner = await db.owners.find_one({"email2": email.lower().strip()}, {"_id": 0})
    if not owner:
        raise HTTPException(404, "Aucune fiche proprietaire ne correspond a votre compte. Contactez le syndic.")
    return owner


async def _resolve_owner_ids(db, request: Request) -> tuple:
    """Iter90df : retourne (owner_ids, primary_owner) pour un proprietaire qui
    peut avoir plusieurs fiches (une par ACP).

    Match strict par email principal + email2 (aucun matching flou).
    Toujours normalise en lowercase.

    Le `primary_owner` (premier match, prefere celui qui a un tier_accounts non
    vide) est retourne pour les infos d'identite : nom, vcs_code, contact...
    """
    email = getattr(request.state, "user_email", "") or ""
    if not email:
        raise HTTPException(401, "Not authenticated")
    email_lower = email.lower().strip()
    # Chercher tous les owners par email ou email2
    cur = db.owners.find(
        {"$or": [{"email": email_lower}, {"email2": email_lower}]},
        {"_id": 0},
    )
    owners = await cur.to_list(50)
    if not owners:
        raise HTTPException(
            404,
            "Aucune fiche proprietaire ne correspond a votre compte. Contactez le syndic.",
        )
    # Choisir un primary : preferer celui avec tier_accounts, puis nom non vide
    def _score(o):
        s = 0
        if o.get("tier_accounts"):
            s += 10
        if o.get("name"):
            s += 1
        if o.get("email") == email_lower:  # preference si email principal
            s += 5
        return -s  # sort ascending -> plus haut score en premier
    owners.sort(key=_score)
    owner_ids = [o["id"] for o in owners]
    return owner_ids, owners[0]


def create_owner_portal_router(db):
    router = APIRouter(prefix="/api/owner")

    @router.get("/me")
    async def get_my_owner_profile(request: Request):
        """Owner's own profile (read-only)."""
        return await _resolve_owner(db, request)

    # iter90hw : apercu des comptes bancaires de l'ACP + leurs mouvements
    # iter90i0 : filtre par plage de dates (start_date / end_date) sur les
    # transactions bancaires. Par defaut : 200 transactions max, sinon aucune
    # limite (le proprio a demande la liste complete sur l'exercice).
    @router.get("/bank-accounts/{copropriete_id}")
    async def owner_bank_accounts(
        copropriete_id: str, request: Request,
        limit: int = 200,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Vue transparente pour le proprio : liste des comptes bancaires de
        l'ACP avec solde comptable actuel + mouvements filtres par date.

        Chinese wall : verifie que le proprio a au moins un lot dans l'ACP.
        Les IBAN sont masques (only last 4 digits visibles).
        `start_date` / `end_date` (YYYY-MM-DD) filtrent les mouvements.
        """
        owner_ids, _ = await _resolve_owner_ids(db, request)
        # Verification lot -> ACP
        has_lot = await db.lots.find_one(
            {"copropriete_id": copropriete_id,
             "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "id": 1},
        )
        if not has_lot:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0, "bank_accounts": 1})
        if not copro:
            raise HTTPException(404, "Copropriete introuvable")

        bank_list = copro.get("bank_accounts") or []
        # Construit le filtre date une seule fois (reutilise pour chaque compte)
        date_filter: dict = {}
        if start_date:
            date_filter["$gte"] = start_date
        if end_date:
            date_filter["$lte"] = end_date
        result = []
        for ba in bank_list:
            iban = ba.get("iban", "") or ""
            pcmn = ba.get("pcmn_number", "") or ""
            acc_type = ba.get("account_type", "") or ""
            label = ba.get("label") or ("Compte a vue" if acc_type == "vue" else "Compte epargne")

            # Solde = somme debit - credit sur pcmn_number dans journal_entries
            # (solde COMPTABLE global, independant du filtre de date : le
            # proprio veut voir le solde actuel meme si la periode est passee)
            balance = 0.0
            if pcmn:
                async for e in db.journal_entries.find(
                    {"copropriete_id": copropriete_id,
                     "lines.account_number": pcmn,
                     "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                    {"_id": 0, "lines": 1},
                ):
                    for ln in e.get("lines", []) or []:
                        if ln.get("account_number") == pcmn:
                            balance += float(ln.get("debit", 0) or 0)
                            balance -= float(ln.get("credit", 0) or 0)

            # Mouvements bancaires : filtre par IBAN + plage de dates
            # iter90i2 : les transactions sont liees au compte via
            # `bank_statements.account_number` (IBAN normalise sans espace).
            # Le champ `iban` sur bank_transactions n'existe pas dans les
            # imports CODA/Optipro standards.
            iban_norm = iban.replace(" ", "").upper()
            statement_ids: list[str] = []
            if iban_norm:
                async for st in db.bank_statements.find(
                    {"copropriete_id": copropriete_id,
                     "account_number": {"$regex": f"^{iban_norm}$", "$options": "i"}},
                    {"_id": 0, "id": 1},
                ):
                    if st.get("id"):
                        statement_ids.append(st["id"])
            # Base query : statement_id in liste ci-dessus OU champ iban legacy
            or_clauses: list[dict] = []
            if statement_ids:
                or_clauses.append({"statement_id": {"$in": statement_ids}})
            if iban:
                # Compat retro : transactions avec un champ `iban` explicite
                # (importe hors flow statement). Match tolerant aux espaces.
                iban_regex = f"^{iban_norm}$"
                or_clauses.append({"iban": {"$regex": iban_regex, "$options": "i"}})
            txn_query: dict = {"copropriete_id": copropriete_id}
            if or_clauses:
                txn_query["$or"] = or_clauses
            if date_filter:
                txn_query["date"] = date_filter
            movements = []
            async for txn in db.bank_transactions.find(
                txn_query,
                {"_id": 0, "date": 1, "amount": 1,
                 "counterparty_name": 1, "counterparty_account": 1,
                 "communication": 1, "matched": 1,
                 "transaction_type": 1, "match_type": 1},
            ).sort("date", -1).limit(limit):
                amount = float(txn.get("amount", 0) or 0)
                # Le sens du montant : le vrai signe est amount ; transaction_type
                # est redondant mais fournit une lecture rapide (credit=entree,
                # debit=sortie de l'ACP).
                movements.append({
                    "date": txn.get("date", ""),
                    "amount": amount,
                    "counterparty": txn.get("counterparty_name", "") or "",
                    "counterparty_account": txn.get("counterparty_account", "") or "",
                    "communication": (txn.get("communication", "") or "")[:120],
                    "matched": bool(txn.get("matched")),
                    "transaction_type": txn.get("transaction_type") or (
                        "credit" if amount >= 0 else "debit"
                    ),
                    "match_type": txn.get("match_type", "") or "",
                })

            # Total du nombre de mouvements sur la periode (peut depasser limit)
            total_count = await db.bank_transactions.count_documents(txn_query)

            # Masquage IBAN : BE04XXXXXXXX9331
            masked = iban
            if iban and len(iban) >= 8:
                masked = iban[:4] + "X" * (len(iban) - 8) + iban[-4:]

            result.append({
                "iban": masked,
                "type": acc_type,
                "label": label,
                "pcmn_number": pcmn,
                "balance": round(balance, 2),
                "is_default": bool(ba.get("is_default")),
                "recent_movements": movements,
                "movements_total_count": total_count,
                "movements_limit": limit,
            })
        return {
            "copropriete_id": copropriete_id,
            "bank_accounts": result,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    # iter90hx : QR code de paiement EPC069-12 (norme SEPA europeenne).
    # Format standard reconnu par toutes les apps bancaires belges (Belfius,
    # BNP, ING, KBC, Bpost, etc). Le proprio scanne -> le paiement est prerempli.
    @router.get("/payment-qr/{copropriete_id}")
    async def owner_payment_qr(copropriete_id: str, request: Request, amount: Optional[float] = None):
        """Genere un QR code EPC069-12 pour paiement sur le compte a vue de l'ACP.

        Le contenu est :
          - Beneficiaire : nom de l'ACP + IBAN du compte 'vue' par defaut
          - Montant : parametre optionnel `amount` (sinon solde debiteur actuel)
          - Communication structuree : VCS du proprio

        Retourne un PNG.
        """
        owner_ids, primary = await _resolve_owner_ids(db, request)
        # Chinese wall : verifier lot dans l'ACP
        has_lot = await db.lots.find_one(
            {"copropriete_id": copropriete_id,
             "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "id": 1},
        )
        if not has_lot:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")

        copro = await db.coproprietes.find_one(
            {"id": copropriete_id},
            {"_id": 0, "name": 1, "bank_accounts": 1},
        )
        if not copro:
            raise HTTPException(404, "Copropriete introuvable")

        # Choisir le compte "vue" par defaut (fallback : premier)
        bank_list = copro.get("bank_accounts") or []
        vue = next((b for b in bank_list if b.get("account_type") == "vue"), None)
        if not vue:
            vue = bank_list[0] if bank_list else None
        if not vue or not vue.get("iban"):
            raise HTTPException(400, "Aucun compte bancaire configure pour cette ACP")

        iban = (vue.get("iban") or "").replace(" ", "").upper()
        bic = (vue.get("bic") or "NOTPROVIDED").replace(" ", "").upper()
        beneficiary_name = (copro.get("name") or "ACP")[:70]

        # Recuperer le VCS du proprio pour cette ACP
        all_owners_docs = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(len(owner_ids))
        vcs_code = ""
        for o in all_owners_docs:
            if o.get("vcs_code"):
                vcs_code = o["vcs_code"]
                break

        # Montant : parametre explicite sinon solde debiteur reel du proprio
        pay_amount = 0.0
        if amount is not None and amount > 0:
            pay_amount = round(float(amount), 2)
        else:
            # Recalculer le solde tier sur cette ACP
            je_q = {"copropriete_id": copropriete_id,
                    "reversed": {"$ne": True}, "is_reversal": {"$ne": True}}
            async for e in db.journal_entries.find(je_q, {"_id": 0, "lines": 1}):
                for ln in e.get("lines", []) or []:
                    if ln.get("third_party_id") in set(owner_ids):
                        pay_amount += float(ln.get("debit", 0) or 0)
                        pay_amount -= float(ln.get("credit", 0) or 0)
            pay_amount = round(pay_amount, 2) if pay_amount > 0.01 else 0.0

        # Format EPC069-12 (11 lignes fixes) - specifie par European Payments Council
        # cf https://www.europeanpaymentscouncil.eu/document-library/guidance-documents/quick-response-code-guidelines-enable-data-capture-initiation
        lines = [
            "BCD",                          # Service Tag (fixe)
            "002",                          # Version (002 = permet BIC vide)
            "1",                            # Character set (1 = UTF-8)
            "SCT",                          # SEPA Credit Transfer identification
            bic if bic else "",             # BIC du beneficiaire (facultatif si vide/002)
            beneficiary_name,               # Nom du beneficiaire (max 70)
            iban,                           # IBAN du beneficiaire
            f"EUR{pay_amount:.2f}" if pay_amount > 0 else "",  # Montant (facultatif)
            "",                             # Purpose (facultatif)
            vcs_code or "",                 # Structured reference (VCS)
            "",                             # Unstructured remittance (mutuellement exclusif avec VCS)
        ]
        payload = "\n".join(lines)

        import qrcode
        from io import BytesIO
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        from fastapi.responses import Response
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "X-Payment-Amount": f"{pay_amount:.2f}",
                "X-Payment-VCS": vcs_code or "",
                "X-Payment-IBAN": iban,
                "X-Payment-Beneficiary": beneficiary_name,
            },
        )

    @router.get("/coproprietes")
    async def my_coproprietes(request: Request):
        """ACPs where the owner has at least one lot (across all owner fiches matching email)."""
        owner_ids, _ = await _resolve_owner_ids(db, request)
        # Lots owned (single or multi-owner) - iter90df : accepte multi-fiches
        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if not copro_ids:
            return []
        coproprietes = await db.coproprietes.find(
            {"id": {"$in": copro_ids}}, {"_id": 0}
        ).sort("name", 1).to_list(100)
        # iter90cz : fallback quotity depuis la cle generale (defaut)
        # si `lot.quotity` est vide/zero.
        # Utile pour les ACPs recentes ou les lots crees sans quotity.
        keys_by_copro = {}
        if copro_ids:
            default_keys = await db.distribution_keys.find(
                {"copropriete_id": {"$in": copro_ids}, "is_default": True},
                {"_id": 0, "copropriete_id": 1, "lots": 1},
            ).to_list(len(copro_ids) * 2)
            for k in default_keys:
                keys_by_copro[k["copropriete_id"]] = {
                    kl.get("lot_id"): float(kl.get("share", 0) or 0)
                    for kl in (k.get("lots") or [])
                }
        # Attach lots to each ACP with quotity fallback
        for c in coproprietes:
            c["my_lots"] = [l for l in lots if l.get("copropriete_id") == c["id"]]
            fallback_shares = keys_by_copro.get(c["id"], {})
            for lt in c["my_lots"]:
                if not float(lt.get("quotity", 0) or 0):
                    fs = fallback_shares.get(lt["id"], 0)
                    if fs > 0:
                        lt["quotity_effective"] = fs
                        lt["quotity_source"] = "cle_generale"
                else:
                    lt["quotity_effective"] = lt.get("quotity", 0)
                    lt["quotity_source"] = "lot"
            c["my_total_quotity"] = sum(
                lt.get("quotity_effective", 0) or lt.get("quotity", 0)
                for lt in c["my_lots"]
            )
        # iter90fy : signale aux frontends si un exercice cloture existe
        # pour cette ACP -> permet au portail proprietaire d'afficher ou
        # masquer le bouton "Telecharger mon decompte annuel" (l'utilisateur
        # veut que le decompte soit uniquement dispo apres cloture).
        if copro_ids:
            closed_fy = await db.fiscal_years.find(
                {"copropriete_id": {"$in": copro_ids}, "status": "closed"},
                {"_id": 0, "copropriete_id": 1, "id": 1, "name": 1, "end_date": 1},
                sort=[("end_date", -1)],
            ).to_list(len(copro_ids) * 20)
            latest_closed_by_cid = {}
            for fy in closed_fy:
                cid = fy.get("copropriete_id")
                if cid and cid not in latest_closed_by_cid:
                    latest_closed_by_cid[cid] = fy
            for c in coproprietes:
                fy = latest_closed_by_cid.get(c["id"])
                c["has_closed_fiscal_year"] = bool(fy)
                c["latest_closed_fiscal_year"] = fy or None
        return coproprietes

    @router.get("/dashboard")
    async def my_dashboard(request: Request):
        """Aggregated owner overview across all ACPs.

        Iter90dd : le solde est desormais calcule via le GRAND LIVRE
        (journal_entries + tp_owner_id) au lieu de fund_calls.distribution.
        C'est aligne avec la balance de tiers admin (source de verite comptable).

        Justification : apres une mutation, les fund_calls restent attribues
        au vendeur dans leur `distribution[]`, tandis que les OD MUT-P
        transferent la quote-part vers l'acquereur au niveau des journal_entries.
        Un calcul base sur `distribution` donne 0 EUR pour l'acquereur alors que
        son compte tier est bien debiteur.
        """
        # Iter90df : support multi-fiches owner (une par ACP) via email match
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        # Compat avec le reste du code (single-owner variables)
        owner = primary_owner
        owner_id_set = set(owner_ids)

        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})

        total_called = 0.0
        total_paid = 0.0
        pending_calls = []
        stats_by_acp: dict = {}  # {copro_id: {called, paid, balance, status}}

        # ===============================================================
        # 1) Solde comptable via journal_entries (aligne balance de tiers)
        # ===============================================================
        if copro_ids:
            je_q = {"copropriete_id": {"$in": copro_ids},
                    "reversed": {"$ne": True}, "is_reversal": {"$ne": True}}
            entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(200000)

            # Iter90df : comptes tiers agreges (tous les owners du meme email)
            tier_accounts_by_copro = {}
            owners_by_id = {}
            all_owners_docs = await db.owners.find(
                {"id": {"$in": owner_ids}}, {"_id": 0}
            ).to_list(len(owner_ids))
            for o in all_owners_docs:
                owners_by_id[o["id"]] = o
                for cp, tacc in (o.get("tier_accounts") or {}).items():
                    if cp not in tier_accounts_by_copro:
                        tier_accounts_by_copro[cp] = set()
                    for a in (tacc.get("provisions", ""), tacc.get("reserve", "")):
                        if a:
                            tier_accounts_by_copro[cp].add(a)
            # Init pour toutes les ACPs ou l'owner a un lot
            for cp in copro_ids:
                if cp not in tier_accounts_by_copro:
                    tier_accounts_by_copro[cp] = set()

            # Init stats
            for cp in copro_ids:
                stats_by_acp[cp] = {"total_called": 0.0, "total_paid": 0.0, "balance": 0.0, "status": "solde"}

            # iter90g3 : dedup defensive. Empeche de compter deux fois la meme
            # ligne d'ecriture (par exemple si `journal_entries` contient un
            # doublon accidentel, ou si un backfill a cree deux MUT-* pour un
            # meme lot/mutation). Cle : (entry_id, line_index). En cas d'id
            # vide, utilise un index de position pour eviter les collisions.
            seen_lines = set()
            for entry_pos, e in enumerate(entries):
                cp = e.get("copropriete_id", "")
                if cp not in stats_by_acp:
                    continue
                entry_id = e.get("id") or f"__pos_{entry_pos}"
                valid_accs = tier_accounts_by_copro.get(cp, set())
                for line_idx, ln in enumerate(e.get("lines", []) or []):
                    tpid = ln.get("third_party_id")
                    acc = ln.get("account_number", "")
                    # Ligne concernee : tp_owner_id in owner_ids OU (tpid vide ET compte in valid_accs)
                    if tpid in owner_id_set or (not tpid and acc in valid_accs):
                        line_key = (entry_id, line_idx)
                        if line_key in seen_lines:
                            continue
                        seen_lines.add(line_key)
                        d_val = float(ln.get("debit", 0) or 0)
                        c_val = float(ln.get("credit", 0) or 0)
                        # DEBIT sur tier = charge appelee ; CREDIT = paiement/reduction
                        stats_by_acp[cp]["total_called"] += d_val
                        stats_by_acp[cp]["total_paid"] += c_val

            # Bank txns non lettres reconnus par VCS -> credit additionnel
            # Iter90df : accepte les VCS de toutes les fiches owner (multi-ACP)
            vcs_digits_set = {
                o.get("vcs_digits", "")
                for o in all_owners_docs if o.get("vcs_digits")
            }
            if vcs_digits_set:
                unmatched_all = await db.bank_transactions.find(
                    {"copropriete_id": {"$in": copro_ids}, "matched": False},
                    {"_id": 0, "amount": 1, "communication": 1, "copropriete_id": 1},
                ).to_list(50000)
                for t in unmatched_all:
                    comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                    if comm in vcs_digits_set:
                        cp = t.get("copropriete_id", "")
                        if cp in stats_by_acp:
                            stats_by_acp[cp]["total_paid"] += abs(t.get("amount", 0))

            # Consolide par ACP + total global
            for cp, st in stats_by_acp.items():
                st["total_called"] = round(st["total_called"], 2)
                st["total_paid"] = round(st["total_paid"], 2)
                st["balance"] = round(st["total_called"] - st["total_paid"], 2)
                st["status"] = "debiteur" if st["balance"] > 0.01 else ("crediteur" if st["balance"] < -0.01 else "solde")
                total_called += st["total_called"]
                total_paid += st["total_paid"]

            # ===============================================================
            # 2) Pending calls : parcourir les fund_calls mais utiliser
            #    tp_owner_id des VE pour retrouver ceux qui concernent
            #    reellement le proprietaire actuel (post-mutation aussi).
            # ===============================================================
            all_fund_calls = await db.fund_calls.find(
                {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
            ).sort("due_date", 1).to_list(10000)

            # Map fund_call_id -> journal_entry lines (VE) sur tier owner
            fc_ids = [fc.get("id") for fc in all_fund_calls if fc.get("id")]
            ve_by_fc = {}
            if fc_ids:
                ve_entries = await db.journal_entries.find({
                    "copropriete_id": {"$in": copro_ids},
                    "journal_type": "VE",
                    "fund_call_id": {"$in": fc_ids},
                    "reversed": {"$ne": True},
                    "is_reversal": {"$ne": True},
                }, {"_id": 0}).to_list(50000)
                for ve in ve_entries:
                    fcid = ve.get("fund_call_id")
                    cp = ve.get("copropriete_id", "")
                    valid_accs = tier_accounts_by_copro.get(cp, set())
                    for ln in ve.get("lines", []) or []:
                        tpid = ln.get("third_party_id")
                        acc = ln.get("account_number", "")
                        if tpid in owner_id_set or (not tpid and acc in valid_accs):
                            amt = float(ln.get("debit", 0) or 0)
                            if amt > 0.01:
                                ve_by_fc[fcid] = ve_by_fc.get(fcid, 0.0) + amt

            # Determiner quels fund_calls sont "impayes" pour ce proprietaire
            # (via balance globale + heuristique proportion)
            for fc in all_fund_calls:
                fcid = fc.get("id")
                amt_owed_originally = ve_by_fc.get(fcid, 0.0)
                if amt_owed_originally < 0.01:
                    continue  # Ce fund_call ne concerne pas ce proprietaire
                # Heuristique : si distribution[owner_id].paid=true, on considere paye
                # Sinon, on marque en attente (le detail est fait via /movements)
                dist = next((d for d in fc.get("distribution", [])
                            if d.get("owner_id") in owner_id_set), None)
                is_paid = dist.get("paid", False) if dist else False
                if not is_paid:
                    pending_calls.append({
                        "fund_call_name": fc.get("name", ""),
                        "due_date": fc.get("due_date", ""),
                        "amount": round(amt_owed_originally, 2),
                        "vcs_code": (dist or {}).get("vcs_code", owner.get("vcs_code", "")),
                        "copropriete_id": fc.get("copropriete_id", ""),
                    })

        balance = round(total_called - total_paid, 2)
        return {
            "owner": owner,
            "stats": {
                "coproprietes_count": len(copro_ids),
                "lots_count": len(lots),
                "total_called": round(total_called, 2),
                "total_paid": round(total_paid, 2),
                "balance": balance,
                "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
                "pending_calls_count": len(pending_calls),
            },
            "stats_by_acp": stats_by_acp,  # iter90dd : filtre cote frontend
            "pending_calls": pending_calls[:15],
        }

    @router.get("/movements")
    async def my_movements(
        request: Request,
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Iter90dd : mouvements du compte tiers du proprietaire (grand livre).

        Filtre cote source de verite (journal_entries + tp_owner_id) et retourne
        pour chaque ligne : date, type, description, debit, credit, running_balance,
        reference (fund_call name, invoice number, etc.).

        Le detail est celui vu par la balance de tiers admin et le PDF "Situation
        de compte". Passe par le filtre period optionnel (start_date/end_date).

        Chinese wall : `copropriete_id` doit etre l'une des ACPs du proprietaire.
        """
        # Iter90df : support multi-fiches owner (une par ACP) via email match
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner = primary_owner
        owner_id_set = set(owner_ids)

        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "copropriete_id": 1},
        ).to_list(1000)
        allowed_copros = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if copropriete_id:
            if copropriete_id not in allowed_copros:
                return {"movements": [], "opening_balance": 0.0, "closing_balance": 0.0}
            copros_target = [copropriete_id]
        else:
            copros_target = allowed_copros
        if not copros_target:
            return {"movements": [], "opening_balance": 0.0, "closing_balance": 0.0}

        # Iter90df : comptes tiers agreges de TOUTES les fiches owner du meme email
        tier_accounts_by_copro = {}
        all_owners_docs = await db.owners.find(
            {"id": {"$in": owner_ids}}, {"_id": 0, "tier_accounts": 1},
        ).to_list(len(owner_ids))
        for o in all_owners_docs:
            for cp, tacc in (o.get("tier_accounts") or {}).items():
                if cp not in tier_accounts_by_copro:
                    tier_accounts_by_copro[cp] = set()
                for a in (tacc.get("provisions", ""), tacc.get("reserve", "")):
                    if a:
                        tier_accounts_by_copro[cp].add(a)
        for cp in copros_target:
            if cp not in tier_accounts_by_copro:
                tier_accounts_by_copro[cp] = set()

        # Query : entries dans la periode + hors extournes
        entry_q = {"copropriete_id": {"$in": copros_target},
                   "reversed": {"$ne": True}, "is_reversal": {"$ne": True}}
        if start_date or end_date:
            entry_q["date"] = {}
            if start_date:
                entry_q["date"]["$gte"] = start_date
            if end_date:
                entry_q["date"]["$lte"] = end_date

        entries = await db.journal_entries.find(entry_q, {"_id": 0}).sort("date", 1).to_list(100000)

        # Fetch fund_call names + invoice numbers pour enrichir descriptions
        fc_ids = list({e.get("fund_call_id", "") for e in entries if e.get("fund_call_id")})
        fc_map = {}
        if fc_ids:
            fcs = await db.fund_calls.find({"id": {"$in": fc_ids}}, {"_id": 0, "id": 1, "name": 1, "due_date": 1}).to_list(len(fc_ids))
            fc_map = {f["id"]: f for f in fcs}

        # Opening balance (avant start_date) - calcule sur l'ACP filtree
        opening = 0.0
        if start_date:
            pre_q = {"copropriete_id": {"$in": copros_target}, "date": {"$lt": start_date},
                     "reversed": {"$ne": True}, "is_reversal": {"$ne": True}}
            pre_entries = await db.journal_entries.find(pre_q, {"_id": 0, "lines": 1, "copropriete_id": 1}).to_list(100000)
            for pe in pre_entries:
                cp = pe.get("copropriete_id", "")
                valid_accs = tier_accounts_by_copro.get(cp, set())
                for ln in pe.get("lines", []) or []:
                    tpid = ln.get("third_party_id")
                    acc = ln.get("account_number", "")
                    if tpid in owner_id_set or (not tpid and acc in valid_accs):
                        opening += float(ln.get("debit", 0) or 0)
                        opening -= float(ln.get("credit", 0) or 0)

        # iter90hy : regroupement par journal_entry - 1 ligne par "type d'appel"
        # meme si le proprio a plusieurs lots (2 distribution_lines sur un meme
        # fund_call -> 1 ligne aggregee dans les mouvements). Sommes debit/credit
        # sur toutes les lignes de l'entry qui concernent les comptes tiers
        # du proprio.
        movements = []
        running = opening
        for e in entries:
            cp = e.get("copropriete_id", "")
            valid_accs = tier_accounts_by_copro.get(cp, set())
            fcid = e.get("fund_call_id")
            fc_info = fc_map.get(fcid, {}) if fcid else {}
            # iter90hy : agregation multi-lots sur ce journal_entry
            agg_debit = 0.0
            agg_credit = 0.0
            # Description : prendre la premiere line_description non vide sinon entry desc
            first_line_desc = ""
            first_acc = ""
            first_counter = ""
            for ln in e.get("lines", []) or []:
                tpid = ln.get("third_party_id")
                acc = ln.get("account_number", "")
                if not (tpid in owner_id_set or (not tpid and acc in valid_accs)):
                    continue
                d_val = float(ln.get("debit", 0) or 0)
                c_val = float(ln.get("credit", 0) or 0)
                if d_val == 0 and c_val == 0:
                    continue
                agg_debit += d_val
                agg_credit += c_val
                if not first_line_desc:
                    first_line_desc = (ln.get("line_description") or "").strip()
                if not first_acc:
                    first_acc = acc
                if not first_counter:
                    first_counter = (ln.get("counterparty_name") or ln.get("third_party_name") or "").strip()
            if agg_debit == 0 and agg_credit == 0:
                continue
            desc = (first_line_desc or e.get("description") or "").strip()
            if fc_info.get("name") and fc_info["name"] not in desc:
                desc = f"{fc_info['name']} - {desc}" if desc else fc_info["name"]
            # iter90eg : formulation claire pour les mouvements bancaires (FI)
            if e.get("journal_type", "") == "FI":
                ref = (e.get("reference") or "").strip()
                detail_parts = [p for p in (first_counter, ref) if p]
                detail_suffix = f" - {' / '.join(detail_parts)}" if detail_parts else ""
                if agg_credit > 0.001:
                    desc = f"Paiement recu{detail_suffix}"
                elif agg_debit > 0.001:
                    desc = f"Votre remboursement{detail_suffix}"
            running += (agg_debit - agg_credit)
            movements.append({
                "date": e.get("date", ""),
                "journal_type": e.get("journal_type", "OD"),
                "reference": e.get("reference", "") or (e.get("id", "")[:8] if e.get("id") else ""),
                "description": desc,
                "debit": round(agg_debit, 2),
                "credit": round(agg_credit, 2),
                "running_balance": round(running, 2),
                "account_number": first_acc,
                "fund_call_id": fcid or "",
                "fund_call_name": fc_info.get("name", ""),
                "copropriete_id": cp,
                "is_mutation": (e.get("source_type") == "lot_mutation") or (e.get("reference", "") or "").startswith("MUT-"),
            })

        return {
            "movements": movements,
            "opening_balance": round(opening, 2),
            "closing_balance": round(running, 2),
        }

    @router.get("/fund-calls")
    async def my_fund_calls(request: Request, copropriete_id: Optional[str] = None):
        """All fund calls where the owner has a distribution (source: fund_calls doc)."""
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner = primary_owner
        owner_id_set = set(owner_ids)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        all_calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        # Batch fetch des coproprietes (fix N+1)
        copro_ids_needed = list({fc.get("copropriete_id", "") for fc in all_calls if fc.get("copropriete_id")})
        copro_map: dict = {}
        if copro_ids_needed:
            copros = await db.coproprietes.find(
                {"id": {"$in": copro_ids_needed}},
                {"_id": 0, "id": 1, "name": 1, "reference": 1},
            ).to_list(len(copro_ids_needed))
            copro_map = {c["id"]: c for c in copros}
        result = []
        for fc in all_calls:
            my_share = next((d for d in fc.get("distribution", []) if d.get("owner_id") in owner_id_set), None)
            if not my_share:
                continue
            copro = copro_map.get(fc.get("copropriete_id", "")) or {}
            result.append({
                "id": fc["id"],
                "name": fc.get("name", ""),
                "date": fc.get("date", ""),
                "due_date": fc.get("due_date", ""),
                "call_type": fc.get("call_type", ""),
                "copropriete_id": fc.get("copropriete_id", ""),
                "copropriete_name": copro.get("name", ""),
                "copropriete_ref": copro.get("reference", ""),
                "my_amount": my_share.get("amount", 0),
                "my_share": my_share.get("share", 0),
                "vcs_code": my_share.get("vcs_code", owner.get("vcs_code", "")),
                "paid": my_share.get("paid", False),
                "paid_date": my_share.get("paid_date", ""),
            })
        return result

    @router.get("/invoices")
    async def my_invoices_charges(request: Request, copropriete_id: Optional[str] = None):
        """Invoices that affect this owner via distribution_lines (his share).

        iter90dz : fallback match par lot_number normalise si le lot_id
        des distribution_lines ne correspond a aucun lot actuel du proprietaire
        (cas Acacia : re-import Optipro -> nouveaux lot_ids, distribution_lines
        pointent vers anciens lot_ids phantoms).
        """
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, _primary = await _resolve_owner_ids(db, request)
        # Get owner's lots
        lots_q = {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]}
        if copropriete_id:
            lots_q["copropriete_id"] = copropriete_id
        my_lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)
        my_lot_ids = {lt["id"] for lt in my_lots}

        # iter90dz : index par lot_number normalise pour fallback matching
        def _norm_num(s: str) -> str:
            return (str(s or "")).strip().lstrip("0") or "0"
        my_lots_by_number = {_norm_num(lt.get("number", "")): lt for lt in my_lots}

        inv_q = {}
        if copropriete_id:
            inv_q["copropriete_id"] = copropriete_id
        else:
            copro_ids = list({lt["copropriete_id"] for lt in my_lots})
            inv_q["copropriete_id"] = {"$in": copro_ids} if copro_ids else "__none__"

        invoices = await db.invoices.find(inv_q, {"_id": 0}).sort("date", -1).to_list(10000)
        result = []
        for inv in invoices:
            my_amount = 0.0
            for dl in inv.get("distribution_lines", []):
                dl_lot_id = dl.get("lot_id")
                dl_amount = float(dl.get("amount", 0) or 0)
                # iter90dz : essaie d'abord match direct par lot_id
                if dl_lot_id and dl_lot_id in my_lot_ids:
                    my_amount += dl_amount
                    continue
                # iter90dz : fallback match par lot_number (phantom)
                dl_lot_num = _norm_num(dl.get("lot_number", ""))
                if dl_lot_num and dl_lot_num != "0" and dl_lot_num in my_lots_by_number:
                    my_amount += dl_amount
            if abs(my_amount) < 0.01:
                # iter90hv : garder les notes de credit (my_amount < 0) et
                # exclure uniquement les factures dont la quote-part est nulle
                # (le proprio n'est pas concerne du tout).
                continue
            # iter90cz : expose attachments (id + filename + mime) pour lien
            # "Voir la facture" cote portail proprietaire.
            atts = []
            for att in inv.get("attachments", []) or []:
                atts.append({
                    "id": att.get("id", ""),
                    "filename": att.get("filename", ""),
                    "mime_type": att.get("mime_type", "application/pdf"),
                })
            result.append({
                "id": inv["id"],
                "number": inv.get("number", ""),
                "date": inv.get("date", ""),
                "supplier": inv.get("supplier", ""),
                "description": inv.get("description", ""),
                "total_amount": inv.get("total_amount", 0),
                "my_amount": round(my_amount, 2),
                "copropriete_id": inv.get("copropriete_id", ""),
                "status": inv.get("status", "unpaid"),
                "category": inv.get("category", ""),
                "attachments": atts,
            })
        return result

    @router.get("/invoices/{invoice_id}/attachments/{attachment_id}/download")
    async def download_owner_invoice_attachment(
        invoice_id: str, attachment_id: str, request: Request,
        disposition: str = "attachment",
    ):
        """iter90cz : Download d'une piece jointe de facture pour un
        proprietaire. Verifie que le proprietaire a une part dans cette facture
        (via distribution_lines lot_id) avant de streamer le fichier.
        """
        from fastapi import Response
        from fastapi.responses import FileResponse
        from pathlib import Path as _Path
        from gridfs_storage import get_invoice_attachments_storage
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, _primary = await _resolve_owner_ids(db, request)

        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        # Chinese wall proprietaire : verifie qu'un des lot_id du proprietaire
        # apparait dans distribution_lines.
        # iter90dz : fallback match par lot_number si lot_id phantom.
        my_lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}],
             "copropriete_id": inv.get("copropriete_id", "")},
            {"_id": 0, "id": 1, "number": 1},
        ).to_list(1000)
        my_lot_ids = {lt["id"] for lt in my_lots}
        def _norm_num(s: str) -> str:
            return (str(s or "")).strip().lstrip("0") or "0"
        my_lot_nums = {_norm_num(lt.get("number", "")) for lt in my_lots}
        has_share = False
        for dl in (inv.get("distribution_lines") or []):
            amt = float(dl.get("amount", 0) or 0)
            if amt <= 0:
                continue
            if dl.get("lot_id") in my_lot_ids:
                has_share = True
                break
            dl_num = _norm_num(dl.get("lot_number", ""))
            if dl_num and dl_num != "0" and dl_num in my_lot_nums:
                has_share = True
                break
        if not has_share:
            raise HTTPException(403, "Vous n'avez pas de part dans cette facture")

        att_storage = get_invoice_attachments_storage(db)
        for att in inv.get("attachments", []) or []:
            if att.get("id") != attachment_id:
                continue
            filename = att.get("filename", "facture.pdf")
            media_type = att.get("mime_type", "application/pdf")
            safe_name = filename.replace('"', "")
            disp_header = (
                f'inline; filename="{safe_name}"' if disposition == "inline"
                else f'attachment; filename="{safe_name}"'
            )
            gid = att.get("gridfs_id")
            if gid:
                try:
                    data = await att_storage.download(gid)
                except Exception:
                    raise HTTPException(404, "Fichier introuvable dans GridFS")
                return Response(
                    content=data, media_type=media_type,
                    headers={"Content-Disposition": disp_header},
                )
            path = att.get("stored_path", "")
            if path and _Path(path).exists():
                return FileResponse(
                    path, media_type=media_type,
                    headers={"Content-Disposition": disp_header},
                )
            raise HTTPException(404, "Fichier introuvable")
        raise HTTPException(404, "Piece jointe non trouvee")

    @router.get("/documents")
    async def my_documents(request: Request, copropriete_id: Optional[str] = None):
        """Documents from ACPs where the owner has lots."""
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, _primary = await _resolve_owner_ids(db, request)
        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "copropriete_id": 1}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if copropriete_id:
            if copropriete_id not in copro_ids:
                return []
            copro_ids = [copropriete_id]
        if not copro_ids:
            return []
        docs = await db.documents.find(
            {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
        ).sort("created_at", -1).to_list(1000)
        # Attach category names + ACP names
        cats = await db.document_categories.find(
            {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
        ).to_list(1000)
        cat_by_id = {c["id"]: c["name"] for c in cats}
        coprops = await db.coproprietes.find(
            {"id": {"$in": copro_ids}}, {"_id": 0, "id": 1, "name": 1}
        ).to_list(100)
        copro_by_id = {c["id"]: c["name"] for c in coprops}
        for d in docs:
            d["category_name"] = cat_by_id.get(d.get("category_id", ""), "")
            d["copropriete_name"] = copro_by_id.get(d.get("copropriete_id", ""), "")
            d.pop("stored_path", None)  # don't expose disk path
        return docs

    @router.get("/communications")
    async def my_communications(request: Request, copropriete_id: Optional[str] = None,
                                limit: int = 100):
        """Iter90db : liste des emails envoyes par le syndic au proprietaire.

        Filtre :
        - `to` contient l'email du proprietaire (email principal OU email2 si defini)
          OU `owner_ids` contient l'id du proprietaire
        - Chinese wall : `copropriete_id` doit etre l'une des ACPs du proprietaire
        - `dry_run` : masque les emails en dry_run (pas reellement envoyes)
        """
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner_id_set = set(owner_ids)
        # Collect all emails (main + email2 of all fiches)
        emails_owner = []
        all_owners_docs = await db.owners.find(
            {"id": {"$in": owner_ids}}, {"_id": 0, "email": 1, "email2": 1},
        ).to_list(len(owner_ids))
        for o in all_owners_docs:
            for e in (o.get("email", ""), o.get("email2", "")):
                if e:
                    emails_owner.append(e.lower().strip())
        emails_owner = list(set(emails_owner))

        # ACPs autorisees pour ce proprietaire (chinese wall)
        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "copropriete_id": 1},
        ).to_list(1000)
        allowed_copros = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if copropriete_id:
            if copropriete_id not in allowed_copros:
                return []
            allowed_copros = [copropriete_id]
        if not allowed_copros:
            return []

        # Query : owner_id OR (email in to)
        q = {
            "copropriete_id": {"$in": allowed_copros},
            "dry_run": {"$ne": True},
            "status": {"$ne": "failed"},
            "$or": [
                {"owner_ids": {"$in": owner_ids}},
                {"to": {"$in": emails_owner}} if emails_owner else {"owner_ids": {"$in": owner_ids}},
            ],
        }
        comms = await db.sent_communications.find(q, {"_id": 0, "body_html": 0}).sort("sent_at", -1).to_list(max(1, min(limit, 500)))

        # Enrichir avec ACP name
        copros_map = {}
        if comms:
            copros = await db.coproprietes.find(
                {"id": {"$in": list({c["copropriete_id"] for c in comms if c.get("copropriete_id")})}},
                {"_id": 0, "id": 1, "name": 1},
            ).to_list(100)
            copros_map = {c["id"]: c["name"] for c in copros}
        for c in comms:
            c["copropriete_name"] = copros_map.get(c.get("copropriete_id", ""), "")
        return comms

    @router.get("/communications/{comm_id}")
    async def my_communication_detail(comm_id: str, request: Request):
        """Iter90db : contenu HTML complet d'un email pour visualisation portail."""
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, _primary = await _resolve_owner_ids(db, request)
        owner_id_set = set(owner_ids)
        emails_owner = []
        all_owners_docs = await db.owners.find(
            {"id": {"$in": owner_ids}}, {"_id": 0, "email": 1, "email2": 1},
        ).to_list(len(owner_ids))
        for o in all_owners_docs:
            for e in (o.get("email", ""), o.get("email2", "")):
                if e:
                    emails_owner.append(e.lower().strip())
        emails_owner = list(set(emails_owner))

        comm = await db.sent_communications.find_one({"id": comm_id}, {"_id": 0})
        if not comm:
            raise HTTPException(404, "Communication introuvable")

        # Chinese wall : verifier appartenance
        if comm.get("dry_run") or comm.get("status") == "failed":
            raise HTTPException(404, "Communication indisponible")
        if not any(oid in owner_id_set for oid in (comm.get("owner_ids") or [])):
            # Fallback : email dans to
            to_lower = [str(e).lower().strip() for e in (comm.get("to") or [])]
            if not any(e in to_lower for e in emails_owner):
                raise HTTPException(403, "Acces refuse a cette communication")

        # Verifier ACP
        copro_id = comm.get("copropriete_id", "")
        if copro_id:
            has_lot = await db.lots.find_one(
                {"copropriete_id": copro_id,
                 "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
                {"_id": 0, "id": 1},
            )
            if not has_lot:
                raise HTTPException(403, "Acces refuse a cette communication")

        return comm

    # iter90hs : download de la piece jointe d'une communication (archivee en GridFS)
    @router.get("/communications/{comm_id}/attachment/download")
    async def download_communication_attachment(comm_id: str, request: Request):
        """Retourne la PJ PDF d'une communication - avec verif chinese wall.
        La PJ est archivee dans GridFS `documents` au moment de l'envoi
        (iter90hs) pour permettre au proprio de la consulter a posteriori."""
        owner_ids, _primary = await _resolve_owner_ids(db, request)
        owner_id_set = set(owner_ids)
        emails_owner = []
        all_owners_docs = await db.owners.find(
            {"id": {"$in": owner_ids}}, {"_id": 0, "email": 1, "email2": 1},
        ).to_list(len(owner_ids))
        for o in all_owners_docs:
            for e in (o.get("email", ""), o.get("email2", "")):
                if e:
                    emails_owner.append(e.lower().strip())
        emails_owner = list(set(emails_owner))

        comm = await db.sent_communications.find_one({"id": comm_id}, {"_id": 0})
        if not comm:
            raise HTTPException(404, "Communication introuvable")
        if comm.get("dry_run") or comm.get("status") == "failed":
            raise HTTPException(404, "Communication indisponible")
        # Chinese wall : owner_ids ou email
        if not any(oid in owner_id_set for oid in (comm.get("owner_ids") or [])):
            to_lower = [str(e).lower().strip() for e in (comm.get("to") or [])]
            if not any(e in to_lower for e in emails_owner):
                raise HTTPException(403, "Acces refuse a cette communication")
        # Verifier ACP
        copro_id = comm.get("copropriete_id", "")
        if copro_id:
            has_lot = await db.lots.find_one(
                {"copropriete_id": copro_id,
                 "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
                {"_id": 0, "id": 1},
            )
            if not has_lot:
                raise HTTPException(403, "Acces refuse a cette communication")
        gid = comm.get("attachment_gridfs_id", "")
        if not gid:
            raise HTTPException(404, "Cette communication n'a pas de piece jointe archivee")
        from storage.documents_storage import get_documents_storage
        from fastapi.responses import Response
        storage = get_documents_storage(db)
        try:
            data = await storage.download(gid)
        except Exception:
            raise HTTPException(404, "Fichier introuvable dans le stockage")
        filename = comm.get("attachment_filename") or "document.pdf"
        safe_name = filename.replace('"', "")
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
        )
    async def my_situation(copropriete_id: str, request: Request):
        """Detailed account situation (mouvements) for owner within a specific ACP."""
        # Iter90df : accepte multi-fiches owner via email match
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner = primary_owner
        # Verify owner has lots in this ACP
        my_lots = await db.lots.find(
            {"copropriete_id": copropriete_id,
             "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0}
        ).to_list(100)
        if not my_lots:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")
        my_lot_ids = {lt["id"] for lt in my_lots}
        # iter90dz : fallback match par lot_number pour lot_ids phantoms
        def _norm_num(s: str) -> str:
            return (str(s or "")).strip().lstrip("0") or "0"
        my_lot_nums = {_norm_num(lt.get("number", "")) for lt in my_lots}
        # Iter90df : compat single owner_id pour code aval
        owner_id = my_lots[0].get("owner_id") or (my_lots[0].get("owner_ids") or [owner["id"]])[0]

        movements = []
        # Fund calls - iter90bv : aggreger par (fc_id, name) pour un proprietaire
        # ayant plusieurs lots. Un meme appel apparait sinon N fois.
        fund_calls = await db.fund_calls.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        for fc in fund_calls:
            owner_amount = 0.0
            for d in fc.get("distribution", []):
                if d.get("owner_id") == owner_id:
                    owner_amount += float(d.get("amount", 0) or 0)
            if owner_amount > 0.001:
                movements.append({
                    "date": fc["date"],
                    "description": f"Appel: {fc['name']}",
                    "debit": round(owner_amount, 2),
                    "credit": 0,
                    "type": "appel",
                })

        # Invoice distributions - iter90bv : cumuler tous les lots du proprietaire
        # sur une meme facture en une seule ligne (sinon N lignes par lot).
        # iter90dz : fallback lot_number pour distributions phantoms.
        invoices = await db.invoices.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        for inv in invoices:
            inv_amount = 0.0
            for dl in inv.get("distribution_lines", []):
                dl_lot_id = dl.get("lot_id")
                if dl_lot_id and dl_lot_id in my_lot_ids:
                    inv_amount += float(dl.get("amount", 0) or 0)
                    continue
                dl_num = _norm_num(dl.get("lot_number", ""))
                if dl_num and dl_num != "0" and dl_num in my_lot_nums:
                    inv_amount += float(dl.get("amount", 0) or 0)
            if inv_amount > 0.001:
                movements.append({
                    "date": inv["date"],
                    "description": f"Charge: {inv.get('supplier', '')} - {inv.get('description', '')}",
                    "debit": round(inv_amount, 2),
                    "credit": 0,
                    "type": "charge",
                })

        # Bank transactions matching this owner
        txns = await db.bank_transactions.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(100000)
        for t in txns:
            is_owner = False
            if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
                is_owner = True
            elif not t.get("matched"):
                comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                if comm and owner.get("vcs_digits") and comm == owner["vcs_digits"]:
                    is_owner = True
            if is_owner:
                movements.append({
                    "date": t["date"],
                    "description": f"Paiement: {t.get('communication','') or t.get('counterparty_name','')}",
                    "debit": 0,
                    "credit": abs(t.get("amount", 0)),
                    "type": "paiement",
                })

        movements.sort(key=lambda m: m["date"])
        running = 0.0
        for m in movements:
            running += m["debit"] - m["credit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)
        balance = round(total_debit - total_credit, 2)
        return {
            "owner": owner,
            "lots": my_lots,
            "movements": movements,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": balance,
            "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
        }

    @router.get("/decompte/pdf")
    async def my_decompte_pdf(request: Request, copropriete_id: str, fiscal_year_id: Optional[str] = None,
                              date_from: Optional[str] = None, date_to: Optional[str] = None):
        """Generate the owner's annual statement PDF (for any of his ACPs).

        iter90fy : refactor pour :
        - Utiliser `_resolve_owner_ids` (multi-fiche support) au lieu de
          `_resolve_owner` (mono) - le proprietaire peut avoir des fiches
          distinctes par ACP dont certaines sans email defini. La fiche
          matchant l'ACP demandee est resolue via ses lots.
        - Si `fiscal_year_id` absent : selection auto du dernier exercice
          CLOTURE (`status='closed'`) de l'ACP. Aucun decompte n'est
          produit tant qu'aucun exercice n'est cloture -> le proprietaire
          voit une erreur claire (400) plutot que le decompte provisoire.
        """
        from datetime import datetime, timezone
        from fastapi.responses import StreamingResponse
        import io
        from pdf_decompte import build_decompte_pdf

        owner_ids, primary_owner = await _resolve_owner_ids(db, request)

        # Trouver la fiche du proprietaire pour cette ACP (peut differer du
        # primary si multi-ACP). On matche par lot.owner_id ou owner_ids.
        # iter90g7 : inclut aussi les lots impliques dans une mutation intra-FY
        # via db.mutations, meme si lot.owner_id est bloque sur l'ancien
        # proprietaire (cas bug TEUWEN/MATEXI legacy).
        owner_lots = await db.lots.find(
            {"copropriete_id": copropriete_id,
             "$or": [
                 {"owner_id": {"$in": owner_ids}},
                 {"owner_ids": {"$in": owner_ids}},
             ]},
            {"_id": 0}
        ).to_list(100)
        if not owner_lots:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")

        # Determiner le owner "actif" pour cette ACP (celui qui possede les lots)
        active_owner_id = owner_lots[0].get("owner_id") or (
            owner_lots[0].get("owner_ids", [None])[0]
        )
        owner = await db.owners.find_one({"id": active_owner_id}, {"_id": 0}) if active_owner_id else None
        if not owner:
            # Fallback : primary owner
            owner = primary_owner
        owner_id = owner["id"]

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Fiscal year : auto-select le dernier CLOSED si non specifie
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one(
                {"id": fiscal_year_id, "copropriete_id": copropriete_id},
                {"_id": 0}
            )
            if not fy:
                raise HTTPException(404, "Exercice comptable introuvable pour cette ACP.")
        else:
            # iter90fy : auto-selection du dernier exercice CLOTURE.
            fy = await db.fiscal_years.find_one(
                {"copropriete_id": copropriete_id, "status": "closed"},
                {"_id": 0},
                sort=[("end_date", -1)],
            )
            if not fy:
                raise HTTPException(
                    400,
                    "Aucun exercice comptable cloture pour cette copropriete. "
                    "Votre decompte annuel sera disponible apres cloture par le syndic.",
                )

        # Verrou : decompte annuel uniquement apres cloture de l'exercice
        if fy.get("status", "") != "closed":
            raise HTTPException(
                400,
                f"L'exercice '{fy.get('name','')}' n'est pas cloture. "
                "Le decompte annuel sera disponible apres la cloture par le syndic."
            )

        # iter90g7 : rattrapage - lots impliques dans une mutation intra-FY
        # via db.mutations, meme si lot.owner_id est bloque sur l'ancien
        # proprietaire (cas bug TEUWEN/MATEXI legacy). Prorata iter90g1 gerera
        # le partage vendeur/acheteur.
        muts_owner_op = await db.mutations.find(
            {"copropriete_id": copropriete_id,
             "$or": [{"from_owner_id": {"$in": owner_ids}},
                     {"to_owner_id": {"$in": owner_ids}}],
             "sale_date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0, "lot_id": 1},
        ).to_list(1000)
        existing_lot_ids_op = {l["id"] for l in owner_lots}
        extra_lot_ids_op = {
            m["lot_id"] for m in muts_owner_op
            if m.get("lot_id") and m["lot_id"] not in existing_lot_ids_op
        }
        if extra_lot_ids_op:
            extra_lots_op = await db.lots.find(
                {"id": {"$in": list(extra_lot_ids_op)},
                 "copropriete_id": copropriete_id},
                {"_id": 0}
            ).to_list(100)
            owner_lots.extend(extra_lots_op)

        all_lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(1000)
        invoices = await db.invoices.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)
        dks = await db.distribution_keys.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(1000)

        # iter90g5 : bug fix - mutations doivent etre passees a build_decompte_pdf
        # pour que le prorata (days_owned/days_total) soit applique. Sans cela,
        # un proprietaire qui achete en cours d'exercice paie 100% des charges
        # via son portail au lieu de sa quote-part reelle.
        owner_lot_ids_op = [l["id"] for l in owner_lots]
        mutations_op = await db.mutations.find(
            {"copropriete_id": copropriete_id,
             "lot_id": {"$in": owner_lot_ids_op},
             "sale_date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0},
        ).to_list(1000) if owner_lot_ids_op else []

        # iter90g6 : OD lot_mutation impactant le compte tier de l'owner
        mutation_entries_op = await db.journal_entries.find(
            {"copropriete_id": copropriete_id,
             "source_type": "lot_mutation",
             "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]},
             "reversed": {"$ne": True},
             "is_reversal": {"$ne": True},
             "lines.third_party_id": owner_id},
            {"_id": 0},
        ).to_list(10000)

        # iter90i9 : grand livre canonique du proprio (source Situation) -
        # aligne les totaux du Decompte sur la Situation de compte.
        _tier_vals_op = [
            v for v in ((owner.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).values()
            if isinstance(v, str) and v
        ]
        _owner_ledger_q_op = {
            "copropriete_id": copropriete_id,
            "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]},
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }
        _or_op = [{"lines.third_party_id": owner_id}]
        if _tier_vals_op:
            _or_op.append({"lines.account_number": {"$in": _tier_vals_op}})
        _owner_ledger_q_op["$or"] = _or_op
        owner_ledger_entries_op = await db.journal_entries.find(
            _owner_ledger_q_op, {"_id": 0},
        ).to_list(100000)

        fcs = await db.fund_calls.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Payments
        all_txns = await db.bank_transactions.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(100000)
        vcs_digits = owner.get("vcs_digits", "")
        payments = []
        for t in all_txns:
            is_owner = False
            if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
                is_owner = True
            elif not t.get("matched") and vcs_digits:
                comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                if comm == vcs_digits:
                    is_owner = True
            if is_owner:
                payments.append(t)

        cats = await db.expense_categories.find(
            {"copropriete_id": copro["id"]} if copro.get("id") else {}, {"_id": 0}
        ).to_list(1000)
        pcmn_acc = await db.pcmn_accounts.find(
            {"class_num": 6, "copropriete_id": copro.get("id", "")}, {"_id": 0}
        ).to_list(1000)
        nature_map = {a["number"]: a.get("name", "") for a in pcmn_acc}
        for c in cats:
            nature_map[c["account_number"]] = c["name"]

        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copro, fiscal_year=fy,
            owner_lots=owner_lots, all_lots=all_lots,
            invoices=invoices, distribution_keys=dks,
            fund_calls=fcs, payments=payments,
            expense_accounts_map=nature_map,
            mutations=mutations_op,  # iter90g5 : prorata mutation
            mutation_entries=mutation_entries_op,  # iter90g6 : OD MUT-R/P/F
            owner_ledger_entries=owner_ledger_entries_op,  # iter90i9 : align Situation
        )

        filename = f"decompte_{owner['name'].replace(' ', '_')}_{fy.get('name','').replace(' ', '_')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/fiscal-years/{copropriete_id}")
    async def my_fiscal_years(copropriete_id: str, request: Request):
        """List fiscal years of an ACP where the owner has lots.

        iter90fy : utilise `_resolve_owner_ids` (multi-fiche) au lieu de
        `_resolve_owner` (mono) pour supporter les proprietaires ayant
        des fiches distinctes par ACP.
        """
        owner_ids, _ = await _resolve_owner_ids(db, request)
        # Verify access
        n = await db.lots.count_documents({
            "copropriete_id": copropriete_id,
            "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]
        })
        if n == 0:
            raise HTTPException(403, "Acces refuse")
        years = await db.fiscal_years.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).sort("start_date", -1).to_list(50)
        return years

    @router.get("/movements/pdf")
    async def my_movements_pdf(
        request: Request,
        copropriete_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """iter90g3 : telechargement PDF du grand livre (appels de fonds) pour
        la periode donnee. Reutilise `_build_situation_compte_pdf` qui produit
        le meme document que celui envoye par le syndic dans les communications
        "Situation de compte". Chinese wall enforce via lots.

        Query params :
        - copropriete_id : obligatoire (chinese wall)
        - start_date / end_date : bornes de la periode (typiquement l'exercice
          selectionne dans l'UI). Optionnels.
        """
        from fastapi.responses import StreamingResponse
        import io
        from routes.reports import _build_situation_compte_pdf

        owner_ids, primary_owner = await _resolve_owner_ids(db, request)

        # Trouver la fiche possedant les lots dans CET ACP (multi-fiche support).
        # Meme logique que /decompte/pdf : le primary_owner peut ne pas avoir
        # les lots dans cette ACP si le proprietaire a plusieurs fiches.
        owner_lots = await db.lots.find(
            {"copropriete_id": copropriete_id,
             "$or": [
                 {"owner_id": {"$in": owner_ids}},
                 {"owner_ids": {"$in": owner_ids}},
             ]},
            {"_id": 0, "owner_id": 1, "owner_ids": 1},
        ).to_list(100)
        if not owner_lots:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")
        active_owner_id = owner_lots[0].get("owner_id") or (
            (owner_lots[0].get("owner_ids") or [None])[0]
        )
        if not active_owner_id or active_owner_id not in owner_ids:
            active_owner_id = primary_owner["id"]

        pdf_bytes, filename = await _build_situation_compte_pdf(
            db, active_owner_id, copropriete_id,
            start_date=start_date, end_date=end_date,
            group_by_owner=True,
        )
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


    # ====================================================================
    # iter89 : SELF-SERVICE - le proprio peut modifier ses coords et gerer
    # ses locataires depuis son espace. Le syndic est averti par email.
    # ====================================================================

    class OwnerSelfUpdate(BaseModel):
        first_name: Optional[str] = None
        last_name: Optional[str] = None
        address: Optional[str] = None
        postal_code: Optional[str] = None
        city: Optional[str] = None
        country: Optional[str] = None
        email: Optional[str] = None
        email2: Optional[str] = None
        phone: Optional[str] = None
        phone2: Optional[str] = None

    async def _owner_copropriete_ids(owner_id: str) -> List[str]:
        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0, "copropriete_id": 1}
        ).to_list(1000)
        return list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})

    @router.put("/me")
    async def update_my_profile(data: OwnerSelfUpdate, request: Request):
        """Owner self-updates his coordinates. Trigger syndic email notification."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Build update payload : only whitelisted fields, only non-None values
        update = {}
        diffs = []
        for field, new_val in data.model_dump(exclude_none=True).items():
            if field not in OWNER_SELF_EDITABLE:
                continue
            old_val = owner.get(field, "") or ""
            new_val_str = (new_val or "").strip() if isinstance(new_val, str) else new_val
            if (new_val_str or "") != (old_val or ""):
                update[field] = new_val_str
                diffs.append(f"{field} : '{old_val}' -> '{new_val_str}'")
        if not update:
            return {"updated": False, "message": "Aucune modification detectee", "owner": owner}
        # Recompute `name` if last/first changed
        new_last = update.get("last_name", owner.get("last_name", "")) or ""
        new_first = update.get("first_name", owner.get("first_name", "")) or ""
        if "last_name" in update or "first_name" in update:
            update["name"] = f"{new_last} {new_first}".strip()
        # iter90hz : detecter changement d'email AVANT le write pour renvoyer
        # une invitation apres le save.
        old_email = (owner.get("email") or "").lower().strip()
        new_email = ""
        if "email" in update:
            new_email = (update["email"] or "").lower().strip()
        email_changed = bool(new_email) and (old_email != new_email)
        await db.owners.update_one({"id": owner_id}, {"$set": update})
        updated_owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        # Notify syndic
        copro_ids = await _owner_copropriete_ids(owner_id)
        notif_result = await notify_syndic_of_owner_change(
            db, updated_owner,
            change_type="modifier ses coordonnees",
            summary_lines=diffs,
            copropriete_ids=copro_ids,
        )
        # iter90hz : si l'email a change, sync le user account lie et
        # renvoyer une invitation au NOUVEL email. Le proprio devra alors
        # se reconnecter avec cet email.
        invitation_info = None
        if email_changed:
            try:
                from routes.owner_access import handle_owner_email_change
                # Note : l'"actor" ici est le proprio lui-meme (self-service).
                # On passe l'owner en tant que actor pour le nom / traca.
                invitation_info = await handle_owner_email_change(
                    db, owner_id, old_email, new_email,
                    {"name": updated_owner.get("name", ""),
                     "email": updated_owner.get("email", ""),
                     "role": "owner"},
                )
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning(
                    f"handle_owner_email_change (self) failed for owner {owner_id}: {_e}",
                )
        return {
            "updated": True,
            "owner": updated_owner,
            "notification": notif_result,
            "reinvitation": invitation_info,
        }

    @router.get("/tenants")
    async def my_tenants(request: Request):
        """Tenants associated to the owner's lots only (scope strict)."""
        # Iter90df : multi-fiches owner via email match
        owner_ids, _ = await _resolve_owner_ids(db, request)
        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0, "id": 1, "number": 1, "copropriete_id": 1, "description": 1}
        ).to_list(1000)
        lot_ids = [l["id"] for l in lots]
        if not lot_ids:
            return {"tenants": [], "lots": []}
        # Iter90dg : tenants supportent lot_ids[] OU lot_id single (legacy)
        tenants = await db.tenants.find(
            {"$or": [
                {"lot_ids": {"$in": lot_ids}},
                {"lot_id": {"$in": lot_ids}},
            ]},
            {"_id": 0},
        ).sort("created_at", -1).to_list(1000)
        # Normalise en sortie : chaque tenant expose lot_ids[] (source de verite)
        # meme si stocke en legacy avec lot_id single.
        for t in tenants:
            if not t.get("lot_ids"):
                if t.get("lot_id"):
                    t["lot_ids"] = [t["lot_id"]]
                else:
                    t["lot_ids"] = []
        return {"tenants": tenants, "lots": lots}

    class TenantInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        # Iter90dg : `lot_ids[]` remplace `lot_id` (compat avec ancien front).
        lot_ids: Optional[List[str]] = None
        lot_id: Optional[str] = None  # legacy fallback si le front n'envoie qu'un seul
        lease_start: Optional[str] = ""
        lease_end: Optional[str] = ""
        mailbox_names: Optional[str] = ""  # iter90dg : noms boite/sonnette

    def _resolve_tenant_lot_ids(data: "TenantInput") -> List[str]:
        """Iter90dg : renvoie la liste de lot_ids depuis TenantInput (compat)."""
        if data.lot_ids:
            return [x for x in data.lot_ids if x]
        if data.lot_id:
            return [data.lot_id]
        return []

    @router.post("/tenants")
    async def create_my_tenant(data: TenantInput, request: Request):
        """Owner creates a tenant - the tenant MUST be linked to one of his lots.

        Iter90dg : accepte plusieurs lots (checkbox cote UI).
        """
        # Iter90df : multi-fiches owner
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner = primary_owner
        lot_ids_req = _resolve_tenant_lot_ids(data)
        if not lot_ids_req:
            raise HTTPException(400, "Au moins un lot est requis")
        # Verifier que TOUS les lots appartiennent au proprietaire
        owned_lots = await db.lots.find(
            {"id": {"$in": lot_ids_req},
             "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0},
        ).to_list(len(lot_ids_req))
        if len(owned_lots) != len(set(lot_ids_req)):
            raise HTTPException(403, "Au moins un des lots ne vous appartient pas")
        # Copropriete_id = celle du premier lot (les lots multiples sont dans la meme ACP en general)
        copro_id = owned_lots[0].get("copropriete_id", "")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name.strip(),
            "email": (data.email or "").strip(),
            "phone": (data.phone or "").strip(),
            "lot_ids": lot_ids_req,
            "lot_id": lot_ids_req[0],  # backward compat
            "lease_start": data.lease_start or "",
            "lease_end": data.lease_end or "",
            "mailbox_names": (data.mailbox_names or "").strip(),
            "copropriete_id": copro_id,
            "created_by_owner_id": owner["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.tenants.insert_one(doc)
        # Notify syndic
        copro = await db.coproprietes.find_one(
            {"id": copro_id}, {"_id": 0, "name": 1}
        ) if copro_id else None
        lots_desc = ", ".join(
            f"{l.get('number','')} {(l.get('description') or '').strip()}".strip()
            for l in owned_lots
        )
        await notify_syndic_of_owner_change(
            db, owner,
            change_type="ajouter un locataire",
            summary_lines=[
                f"Locataire : {doc['name']}",
                f"Lots : {lots_desc}",
                f"Email : {doc['email'] or '-'}",
                f"GSM : {doc['phone'] or '-'}",
                f"Bail : {doc['lease_start'] or '-'} -> {doc['lease_end'] or '-'}",
                f"Noms boite/sonnette : {doc['mailbox_names'] or '-'}",
            ],
            copropriete_ids=[copro_id] if copro_id else [],
            copropriete_name=(copro or {}).get("name", ""),
        )
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/tenants/{tenant_id}")
    async def update_my_tenant(tenant_id: str, data: TenantInput, request: Request):
        # Iter90df : multi-fiches owner
        owner_ids, primary_owner = await _resolve_owner_ids(db, request)
        owner = primary_owner
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        # Verifier ownership sur AU MOINS UN des lots actuels
        existing_lot_ids = existing.get("lot_ids") or (
            [existing["lot_id"]] if existing.get("lot_id") else []
        )
        if existing_lot_ids:
            check = await db.lots.find_one(
                {"id": {"$in": existing_lot_ids},
                 "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
                {"_id": 0, "id": 1},
            )
            if not check:
                raise HTTPException(403, "Ce locataire n'est pas dans l'un de vos lots")
        # Nouveaux lots doivent tous appartenir au proprietaire
        lot_ids_req = _resolve_tenant_lot_ids(data)
        if not lot_ids_req:
            raise HTTPException(400, "Au moins un lot est requis")
        owned_lots = await db.lots.find(
            {"id": {"$in": lot_ids_req},
             "$or": [{"owner_id": {"$in": owner_ids}}, {"owner_ids": {"$in": owner_ids}}]},
            {"_id": 0},
        ).to_list(len(lot_ids_req))
        if len(owned_lots) != len(set(lot_ids_req)):
            raise HTTPException(403, "Au moins un des nouveaux lots ne vous appartient pas")
        copro_id = owned_lots[0].get("copropriete_id", existing.get("copropriete_id", ""))
        diffs = []
        new_email = (data.email or "").strip()
        new_phone = (data.phone or "").strip()
        new_mailbox = (data.mailbox_names or "").strip()
        for k, old, new in [
            ("name", existing.get("name", ""), data.name.strip()),
            ("email", existing.get("email", ""), new_email),
            ("phone", existing.get("phone", ""), new_phone),
            ("lease_start", existing.get("lease_start", ""), data.lease_start or ""),
            ("lease_end", existing.get("lease_end", ""), data.lease_end or ""),
            ("mailbox_names", existing.get("mailbox_names", ""), new_mailbox),
            ("lot_ids", existing_lot_ids, lot_ids_req),
        ]:
            if old != new:
                diffs.append(f"{k} : '{old}' -> '{new}'")
        update = {
            "name": data.name.strip(), "email": new_email, "phone": new_phone,
            "lot_ids": lot_ids_req,
            "lot_id": lot_ids_req[0],  # backward compat
            "lease_start": data.lease_start or "",
            "lease_end": data.lease_end or "",
            "mailbox_names": new_mailbox,
            "copropriete_id": copro_id,
        }
        # Iter90dg : purger l'ancien champ rent_amount pour reflet UI
        await db.tenants.update_one(
            {"id": tenant_id},
            {"$set": update, "$unset": {"rent_amount": ""}},
        )
        updated = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if diffs:
            copro = await db.coproprietes.find_one(
                {"id": update["copropriete_id"]}, {"_id": 0, "name": 1}
            )
            await notify_syndic_of_owner_change(
                db, owner,
                change_type=f"modifier le locataire {updated['name']}",
                summary_lines=diffs,
                copropriete_ids=[update["copropriete_id"]] if update.get("copropriete_id") else [],
                copropriete_name=(copro or {}).get("name", ""),
            )
        return updated

    @router.delete("/tenants/{tenant_id}")
    async def delete_my_tenant(tenant_id: str, request: Request):
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        lot = await db.lots.find_one(
            {"id": existing.get("lot_id", ""),
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0, "number": 1, "copropriete_id": 1}
        )
        if not lot:
            raise HTTPException(403, "Ce locataire n'est pas dans l'un de vos lots")
        await db.tenants.delete_one({"id": tenant_id})
        copro = await db.coproprietes.find_one(
            {"id": lot.get("copropriete_id", "")}, {"_id": 0, "name": 1}
        )
        await notify_syndic_of_owner_change(
            db, owner,
            change_type=f"supprimer le locataire {existing.get('name','')}",
            summary_lines=[
                f"Locataire supprime : {existing.get('name','')}",
                f"Lot : {lot.get('number','')}",
            ],
            copropriete_ids=[lot.get("copropriete_id", "")] if lot.get("copropriete_id") else [],
            copropriete_name=(copro or {}).get("name", ""),
        )
        return {"message": "Locataire supprime"}

    return router
