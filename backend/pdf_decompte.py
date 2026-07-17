"""Generateur PDF du decompte annuel proprietaire - VERSION SIMPLIFIEE
Optimise pour LECTURE par des proprietaires non-comptables :
- Pas de jargon technique (Debit/Credit -> "Votre quote-part" / "Vous avez deja paye")
- Dates au format jj/mm/aaaa
- Libelles parlants, mise en page aeree
- Carte recap visible avec solde colore (rouge=a payer / vert=en votre faveur)
- Detail des charges groupees par nature de depense (lisible)
- Instructions de paiement claires (IBAN + communication structuree)
"""
from io import BytesIO
from datetime import datetime
from collections import defaultdict
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
RED = colors.HexColor("#DC2626")
GREEN = colors.HexColor("#16A34A")
RED_BG = colors.HexColor("#FEF3F2")
GREEN_BG = colors.HexColor("#F0FDF4")


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",") + " EUR"


def _fmt_date(s):
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def _addr_block(name, parts):
    lines = [name] + [p for p in parts if p]
    return "<br/>".join(lines)


def build_decompte_pdf(
    owner: dict,
    copropriete: dict,
    fiscal_year: dict,
    owner_lots: list,
    all_lots: list,
    invoices: list,
    distribution_keys: list,
    fund_calls: list,
    payments: list,
    expense_accounts_map: dict = None,
    preview: bool = False,
    syndic_pdf_ctx: dict = None,
    mutations: list = None,
    mutation_entries: list = None,
    owner_ledger_entries: list = None,
) -> bytes:
    """Genere le PDF Decompte annuel pour un proprietaire.

    Si `preview=True`, un filigrane diagonal "APERCU - NON DEFINITIF" est
    superpose sur chaque page (couleur rouge transparente, en travers).

    iter90av : `syndic_pdf_ctx` (optionnel) active le nouveau layout avec logo
    cabinet + adresse destinataire fenetre C6 droite + mentions legales footer.

    iter90g6 : `mutation_entries` (optionnel) contient les OD `source_type=
    lot_mutation` touchant le compte tier du proprietaire pendant le FY
    (MUT-R fonds de roulement, MUT-P prorata, MUT-F futurs). Sans ce
    parametre, un acheteur mid-year voit un decompte incomplet : ses
    transferts de dettes reprises au vendeur ne sont pas imputes, son solde
    apparait faussement crediteur.

    iter90i9 : `owner_ledger_entries` (optionnel) contient TOUTES les JE non
    contre-passees dont une ligne porte `third_party_id = owner_id` OU un
    compte tier du proprietaire (`tier_accounts.[copro_id]`). C'est la MEME
    source que la Situation de compte : quand fourni, on l'utilise pour
    calculer `total_called`, `total_payments` et `balance` afin que le
    Decompte et la Situation restent alignes. Sans ce parametre, on tombe
    dans le calcul legacy base sur `fund_calls.distribution[owner_id]` qui
    peut rater des appels si la distribution pointe vers l'ancien
    proprietaire (bug legacy MATEXI/TEUWEN mutations mid-year).
    """
    use_new_layout = bool(syndic_pdf_ctx)  # iter90hm : nouveau layout TOUJOURS actif si contexte fourni
    from pdf_layout import build_header_with_logo, build_recipient_address_flowable, draw_legal_footer
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=28 * mm if use_new_layout else 18 * mm,
        title=f"Decompte annuel - {owner.get('name','')} - {fiscal_year.get('name','')}"
              + (" (APERCU)" if preview else ""),
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=20, leading=24, textColor=BRAND,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=10, leading=13, textColor=SLATE_500, spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=15, textColor=SLATE_900,
                        spaceAfter=4, fontName="Helvetica-Bold")
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8.5, leading=11, textColor=SLATE_500)
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9.5, leading=12.5)
    right = ParagraphStyle("right", parent=body, alignment=2)

    elems = []

    # ---- HEADER : logo + ACP info (iter90dm) ----
    if use_new_layout:
        elems.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"), copropriete, small,
        ))
        elems.append(Spacer(1, 4 * mm))
        elems.append(build_recipient_address_flowable(owner, small))
        elems.append(Spacer(1, 8 * mm))

    # ---- HEADER : ACP block (toujours affiche, en dessous du logo si new layout) ----
    acp_lines = _addr_block(
        f"<b>{copropriete.get('name','')}</b>",
        [
            copropriete.get("address", ""),
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}".strip(),
            f"BCE : {copropriete.get('bce','')}" if copropriete.get('bce') else "",
        ],
    )
    period_str = (
        f"Exercice <b>{fiscal_year.get('name','')}</b><br/>"
        f"Periode du <b>{_fmt_date(fiscal_year.get('start_date',''))}</b> "
        f"au <b>{_fmt_date(fiscal_year.get('end_date',''))}</b><br/>"
        f"Edite le <b>{datetime.now().strftime('%d/%m/%Y')}</b>"
    )
    header_tbl = Table(
        [[Paragraph(acp_lines, small), Paragraph(period_str, small)]],
        colWidths=[90 * mm, 90 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- TITLE ----
    elems.append(Paragraph("Decompte annuel de vos charges", title_style))
    elems.append(Paragraph(
        "Recapitulatif des charges qui vous concernent et de vos versements pendant l'exercice.",
        sub_style,
    ))
    elems.append(Spacer(1, 4 * mm))

    # ---- DESTINATAIRE BLOCK ----
    owner_addr = _addr_block(
        f"<b>{owner.get('name','')}</b>",
        [
            owner.get("address", ""),
            f"{owner.get('postal_code','')} {owner.get('city','')}".strip(),
            owner.get("country", ""),
        ],
    )
    owner_lot_ids = {l["id"] for l in owner_lots}
    # iter90dz : fallback match par lot_number pour lot_ids phantoms
    def _norm_num(s: str) -> str:
        return (str(s or "")).strip().lstrip("0") or "0"
    owner_lot_nums = {_norm_num(l.get("number", "")) for l in owner_lots}
    # Mapping lot_number -> current lot_id (pour resoudre le phantom vers le vrai)
    owner_lot_id_by_num = {_norm_num(l.get("number", "")): l["id"] for l in owner_lots}

    # iter90g0 : fallback quotity via la cle de repartition GENERALE
    # (is_default=True). Sur les ACPs issues d'un import legacy (Acacia
    # TER, Optipro), le champ `lot.quotity` est vide - les tantiemes
    # vivent dans `distribution_keys[is_default].lots[].share`. Sans ce
    # fallback l'en-tete du decompte affichait "0 tantiemes" et la
    # quote-part globale "0.00% (0/1)". Meme regle qu'iter90ej
    # (`_lot_share`) et iter90ft (compute_bilan_data).
    default_dk = None
    for _dk in (distribution_keys or []):
        if _dk.get("is_default") is True:
            default_dk = _dk
            break
    default_share_map = {}
    default_total = 0.0
    if default_dk:
        for it in (default_dk.get("lots", []) or []):
            if it.get("excluded"):
                continue
            lid = it.get("lot_id")
            q = it.get("share")
            if q is None:
                q = it.get("quotity", 0)
            try:
                q = float(q or 0)
            except Exception:
                q = 0.0
            if lid:
                default_share_map[lid] = q
                default_total += q

    def _lot_quotity(l):
        """Retourne la quotite effective d'un lot avec fallback sur la cle
        generale. Priorite : lot.quotity direct > default_key.lot.share."""
        q = float(l.get("quotity", 0) or 0)
        if q > 0:
            return q
        return default_share_map.get(l.get("id"), 0.0)

    owner_quotity = sum(_lot_quotity(l) for l in owner_lots)
    total_quotity = sum(_lot_quotity(l) for l in all_lots) or 1
    share_pct = owner_quotity / total_quotity * 100

    # iter90fv : detection de la cle de repartition "eau" (nom OU code
    # contenant 'eau', insensible a la casse). L'utilisateur demande que
    # les quotites eau par lot soient visibles dans l'en-tete du PDF
    # decompte, en plus des quotites generales, pour faciliter le
    # controle des repartitions liees a la consommation d'eau.
    water_key = None
    for _dk in (distribution_keys or []):
        _lbl = f"{_dk.get('name','')} {_dk.get('code','')}".lower()
        if "eau" in _lbl and _dk.get("is_default") is not True:
            water_key = _dk
            break
    water_shares = {}
    water_total = 0.0
    if water_key:
        for it in (water_key.get("lots", []) or []):
            if it.get("excluded"):
                continue
            lid = it.get("lot_id")
            q = it.get("share")
            if q is None:
                q = it.get("quotity", 0)
            try:
                q = float(q or 0)
            except Exception:
                q = 0.0
            if lid:
                water_shares[lid] = q
                water_total += q

    def _lot_line(l):
        base = (
            f"Lot <b>{l.get('number','')}</b> - {l.get('description','')} "
            f"({_lot_quotity(l):.0f} tantiemes)"
        )
        # iter90fv : ajoute les quotites eau si la cle existe pour ce lot
        if water_key and l.get("id") in water_shares:
            base += (
                f"<br/><font size='8' color='#64748B'>&nbsp;&nbsp;&nbsp;&nbsp;"
                f"Quotites {water_key.get('name','eau')} : "
                f"<b>{water_shares[l['id']]:.0f}</b>"
                f"{('/' + f'{water_total:.0f}' + ' unites') if water_total > 0 else ''}"
                f"</font>"
            )
        return base
    lots_str = "<br/>".join(_lot_line(l) for l in owner_lots) or "(aucun lot)"

    info_lines = [
        f"<b>Vos lots :</b><br/>{lots_str}",
        "&nbsp;",
        f"<b>Votre quote-part globale :</b> "
        f"<font color='{BRAND.hexval()}'><b>{share_pct:.2f}%</b></font> "
        f"<font size='8' color='#64748B'>({owner_quotity:.0f}/{total_quotity:.0f} tantiemes)</font>",
        "&nbsp;",
        f"<b>Reference communication :</b> "
        f"<font color='{BRAND.hexval()}'><b>{owner.get('vcs_code','')}</b></font>",
    ]
    dest_tbl = Table(
        [[Paragraph(owner_addr, body), Paragraph("<br/>".join(info_lines), small)]],
        colWidths=[90 * mm, 90 * mm],
    )
    dest_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    elems.append(dest_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- COMPUTE TOTALS (style Finlead : Lot -> Cle de repartition -> Compte) ----
    # Build distribution key index : dk_id -> {lots: {lot_id: quotity}, total_quotity}
    # Note: distribution_keys.lots use the field "share" (not "quotity")
    dk_index = {}
    for dk in distribution_keys:
        items = dk.get("lots", []) or []
        lots_map = {}
        for it in items:
            # iter90ac : ignorer les lots explicitement exclus de la cle
            if it.get("excluded"):
                continue
            lot_id = it.get("lot_id")
            # Try "share" first (real schema), fall back to "quotity" for safety
            q = it.get("share")
            if q is None:
                q = it.get("quotity", 0)
            try:
                lots_map[lot_id] = float(q or 0)
            except Exception:
                lots_map[lot_id] = 0.0
        dk_index[dk.get("id")] = {
            "name": dk.get("name", ""),
            "lots": lots_map,
            "total": sum(lots_map.values()),
        }

    def _zero_stats():
        return {"total_dist": 0.0, "owner_amt": 0.0,
                "owner_occ": 0.0, "owner_prop": 0.0, "invoices": []}

    # per_lot_data: lot_id -> { key_id -> { 'accounts': {acc -> stats}, '_invoices_seen': set } }
    per_lot_data = {}
    total_owner_charges = 0.0
    total_occupant_share = 0.0
    total_proprio_share = 0.0
    # iter90fz : bug fix "Montant à répartir" double-comptage.
    # Ancien code : `grand_dist = sum(lot_total_dist)` -> un meme invoice
    # touchant N lots du proprietaire etait compte N fois dans la ligne
    # "Totaux generaux". Signale par l'utilisateur : "tu additions les
    # frais repartis par lots alors qu'il ne s'agit pas d'une addition".
    # Correction : dedup les invoices AU NIVEAU OWNER (et par LOT) pour
    # le total "Montant à répartir" (les colonnes "Part proprietaire" et
    # "Part occupant" restent des sommes de fractions et sont correctes).
    # iter90g1 : avec prorata mutation, on retient le prorata MAX vu pour
    # chaque invoice (ex: proprietaire multi-lots ou un seul a subi une
    # mutation) afin d'eviter de sous-evaluer le "Montant à répartir".
    owner_seen_inv_prorata = {}         # inv_id -> max prorata seen for this owner
    owner_grand_dist_unique = 0.0       # somme des inv_total uniques (prorates)
    lot_seen_inv_ids = {}               # lot_id -> set(inv_id)
    lot_dist_unique = {}                # lot_id -> somme des inv_total_for_lot uniques
    # Aggregate by account globally for the "Recap locataire" summary
    occupant_summary_by_acc = {}  # acc -> {occ_amt, label}

    # iter90el / iter90g1 : calcul du prorata mutation par lot.
    # Pour chaque lot du proprietaire, determine la periode de possession
    # dans l'exercice (start_iso, end_iso). Convention Belge (art. 3.86
    # CDE + iter76 fund calls) : la journee de vente est attribuee a
    # l'ACHETEUR. Le vendeur possede jusqu'a la veille de la vente inclus.
    #
    # iter90g1 : remplace le simple FILTRAGE par date (iter90el) par un vrai
    # PRORATA multiplicatif (days_owned / days_total) applique aux invoices
    # de charges courantes (Class 6). Repond a la pratique Optipro et au
    # scenario "318/365" mentionne par l'utilisateur : les charges annuelles
    # (assurance, entretien) sont partagees entre ancien et nouveau
    # proprietaire au prorata des jours de possession, sans regarder la
    # date exacte de la facture.
    from datetime import date as _date, timedelta as _td
    def _iso_to_date(s):
        try:
            return _date.fromisoformat((s or "")[:10])
        except Exception:
            return None
    fy_start_iso = fiscal_year.get("start_date", "") or ""
    fy_end_iso = fiscal_year.get("end_date", "") or ""
    fy_start_dt = _iso_to_date(fy_start_iso)
    fy_end_dt = _iso_to_date(fy_end_iso)
    fy_days_total = 0
    if fy_start_dt and fy_end_dt:
        fy_days_total = max(1, (fy_end_dt - fy_start_dt).days + 1)

    lot_owned_period = {}   # lot_id -> (start_iso, end_iso, days_owned, fy_days_total)
    owner_id_local = owner.get("id", "")
    muts = mutations or []
    for lt in owner_lots:
        lid = lt.get("id")
        # Debut : par defaut le debut d'exercice ou l'owner l'avait deja
        start_iso = fy_start_iso
        end_iso = fy_end_iso
        lot_muts = sorted(
            [m for m in muts if m.get("lot_id") == lid],
            key=lambda m: (m.get("sale_date") or ""),
        )
        for m in lot_muts:
            sd = (m.get("sale_date") or "")[:10]
            if not sd or not (fy_start_iso <= sd <= fy_end_iso):
                continue
            # Owner ACHETE le lot pendant l'exercice -> possession debute
            # a sd (jour de vente inclus pour l'acheteur, cf iter76).
            if m.get("to_owner_id") == owner_id_local:
                if sd > start_iso:
                    start_iso = sd
            # Owner VEND le lot pendant l'exercice -> possession finit la
            # VEILLE de sd (jour de vente exclu du vendeur, cf iter76). Fix
            # off-by-one iter90g1 : evite le double comptage d'un jour
            # entre vendeur et acheteur.
            if m.get("from_owner_id") == owner_id_local:
                sd_dt = _iso_to_date(sd)
                if sd_dt:
                    day_before = (sd_dt - _td(days=1)).isoformat()
                    if day_before < end_iso:
                        end_iso = day_before
        # Prorata jours
        s_dt = _iso_to_date(start_iso)
        e_dt = _iso_to_date(end_iso)
        if s_dt and e_dt and e_dt >= s_dt:
            days_owned = (e_dt - s_dt).days + 1
        else:
            days_owned = 0
        lot_owned_period[lid] = (start_iso, end_iso, days_owned, fy_days_total)

    # Pre-compute "tantiemes par defaut" fallback (sum of all lot quotities in ACP)
    default_total_quotity = sum(float(l.get("quotity", 0) or 0) for l in all_lots) or 1.0

    # Special pseudo distribution_key for invoices without explicit key
    NO_KEY = "_default_tantiemes"
    if NO_KEY not in dk_index:
        dk_index[NO_KEY] = {
            "name": "Tantiemes (par defaut)",
            "lots": {l["id"]: float(l.get("quotity", 0) or 0) for l in all_lots},
            "total": default_total_quotity,
        }

    for inv in invoices:
        raw_key = inv.get("distribution_key_id", "") or ""
        acc = inv.get("account_number", "") or "_other"
        occ_pct = float(inv.get("occupant_pct", 0) or 0)
        inv_total = float(inv.get("total_amount", 0) or 0)
        # Compute owner share per lot for this invoice
        lot_share = {}
        explicit_lines = inv.get("distribution_lines", []) or []
        has_explicit = any(
            (dl.get("lot_id") and float(dl.get("amount", 0) or 0) > 0.001)
            for dl in explicit_lines
        )

        if has_explicit:
            # Use explicit distribution_lines
            # iter90dz : match direct par lot_id, fallback par lot_number
            key_id = raw_key or "_none"
            for dl in explicit_lines:
                lid = dl.get("lot_id")
                amt = float(dl.get("amount", 0) or 0)
                if lid in owner_lot_ids:
                    lot_share[lid] = lot_share.get(lid, 0.0) + amt
                    continue
                # Fallback iter90dz : match par lot_number
                dl_num = _norm_num(dl.get("lot_number", ""))
                if dl_num and dl_num != "0" and dl_num in owner_lot_nums:
                    real_lid = owner_lot_id_by_num[dl_num]
                    lot_share[real_lid] = lot_share.get(real_lid, 0.0) + amt
        else:
            # Fallback : split inv_total by tantiemes
            # Use the configured distribution key if present, else default tantiemes
            key_id = raw_key if (raw_key and raw_key in dk_index) else NO_KEY
            dk_info = dk_index.get(key_id, dk_index[NO_KEY])
            lots_map = dk_info.get("lots") or {}
            total_q = dk_info.get("total") or default_total_quotity
            if total_q <= 0:
                continue  # cannot split
            for lid in owner_lot_ids:
                q = lots_map.get(lid)
                if not q:  # not in the key -> skip
                    continue
                lot_share[lid] = round(inv_total * q / total_q, 2)

        for lot_id, amt_owner in lot_share.items():
            if abs(amt_owner) < 0.001 and abs(inv_total) < 0.001:
                continue
            # iter90g1 : prorata mutation. Applique un facteur multiplicatif
            # (days_owned / days_total) sur la quote-part du proprietaire
            # et sur le montant total distribue au lot. Remplace le simple
            # filtrage par date (iter90el) qui produisait un ecart avec
            # Optipro sur les charges annuelles (assurance, entretien).
            # Convention : "Ancien paie prorata sur la periode ou il
            # possedait, nouveau paie le reste" (Interpretation B).
            lot_prorata = 1.0
            period = lot_owned_period.get(lot_id)
            if period:
                _s, _e, days_owned, days_total = period
                if days_owned <= 0:
                    continue  # Owner n'a pas possede ce lot dans l'exercice
                if days_total > 0 and days_owned < days_total:
                    lot_prorata = days_owned / days_total
            if lot_prorata < 1.0:
                amt_owner = round(amt_owner * lot_prorata, 2)
                inv_total_for_lot = round(inv_total * lot_prorata, 2)
            else:
                inv_total_for_lot = inv_total
            amt_occ = round(amt_owner * occ_pct / 100, 2)
            amt_prop = round(amt_owner - amt_occ, 2)

            lot_bucket = per_lot_data.setdefault(lot_id, {})
            key_bucket = lot_bucket.setdefault(key_id, {"accounts": {}, "_invoices_seen": set()})
            acc_bucket = key_bucket["accounts"].setdefault(acc, _zero_stats())

            acc_bucket["owner_amt"] += amt_owner
            acc_bucket["owner_occ"] += amt_occ
            acc_bucket["owner_prop"] += amt_prop

            # Store invoice detail for line-by-line breakdown under each account
            # iter85f : pour les factures multi-lignes, si une ligne touche ce
            # compte (acc) et a une description, on l'affiche au lieu de la
            # description globale. Plusieurs lignes sur le meme compte ->
            # descriptions concatenees par " - ".
            inv_lines_raw = inv.get("lines") or []
            line_descs = []
            for _li in inv_lines_raw:
                if _li.get("account_number") == acc:
                    _d = (_li.get("description") or "").strip()
                    if _d:
                        line_descs.append(_d)
            base_desc = inv.get("description", "") or ""
            final_desc = " - ".join(line_descs) if line_descs else base_desc

            acc_bucket["invoices"].append({
                "date": inv.get("date", ""),
                "supplier": inv.get("supplier", "") or "",
                "description": final_desc,
                "reference": inv.get("reference", "") or inv.get("invoice_number", "") or "",
                "total_amount": inv_total_for_lot,
                "total_amount_full": inv_total,
                "prorata": lot_prorata,
                "owner_amt": amt_owner,
                "owner_occ": amt_occ,
                "owner_prop": amt_prop,
                "auto_split": not has_explicit,  # flag for UI hint
            })

            # Dedup total_to_distribute per (lot, key, acc, invoice)
            inv_id = inv.get("id") or inv.get("internal_ref", "")
            seen_key = (acc, inv_id)
            if seen_key not in key_bucket["_invoices_seen"]:
                key_bucket["_invoices_seen"].add(seen_key)
                acc_bucket["total_dist"] += inv_total_for_lot

            # iter90fz : dedup au niveau LOT et OWNER pour eviter le
            # double-comptage dans les totaux "Montant à répartir".
            # iter90g1 : le montant deduplique est deja prorate par lot.
            # Pour l'owner, on retient le prorata MAX (cas rare : owner a
            # deux lots, un seul a subi mutation).
            lot_seen = lot_seen_inv_ids.setdefault(lot_id, set())
            if inv_id not in lot_seen:
                lot_seen.add(inv_id)
                lot_dist_unique[lot_id] = lot_dist_unique.get(lot_id, 0.0) + inv_total_for_lot
            prev_prorata = owner_seen_inv_prorata.get(inv_id, 0.0)
            if lot_prorata > prev_prorata:
                # Ajoute le delta pour convergerf vers max(prorata) * inv_total
                owner_grand_dist_unique += inv_total * (lot_prorata - prev_prorata)
                owner_seen_inv_prorata[inv_id] = lot_prorata

            total_owner_charges += amt_owner
            total_occupant_share += amt_occ
            total_proprio_share += amt_prop

            # Global occupant aggregation by account (for recap locataire)
            if amt_occ > 0.001:
                e = occupant_summary_by_acc.setdefault(acc, {"amount": 0.0, "label": ""})
                e["amount"] += amt_occ

    # Owner share of fund calls + payments
    # Distinguer les 3 types d'appels :
    #   - provisions : consommees par les charges (boni/mali)
    #   - reserve : alimente le fonds de reserve (definitif, ne se rembourse pas)
    #   - roulement : alimente le fonds de roulement (definitif)
    total_called = 0.0
    total_called_provisions = 0.0
    total_called_reserve = 0.0
    total_called_roulement = 0.0
    fund_calls_owner = []
    for fc in fund_calls:
        share = next((d for d in fc.get("distribution", []) if d.get("owner_id") == owner["id"]), None)
        if not share:
            continue
        amt = float(share.get("amount", 0) or 0)
        total_called += amt
        # Repartition de l'appel pour ce proprietaire en 3 parts
        fc_total = float(fc.get("total_amount", 0) or 0) or 1.0
        fc_reserve = float(fc.get("reserve_amount", 0) or 0)
        fc_roulement = float(fc.get("roulement_amount", 0) or 0)
        fc_provisions = max(0.0, fc_total - fc_reserve - fc_roulement)
        # Quote-part de l'owner dans chacune des 3 parts proportionnellement
        owner_ratio = (amt / fc_total) if fc_total > 0 else 0.0
        share_reserve = round(fc_reserve * owner_ratio, 2)
        share_roulement = round(fc_roulement * owner_ratio, 2)
        share_provisions = round(amt - share_reserve - share_roulement, 2)
        total_called_provisions += share_provisions
        total_called_reserve += share_reserve
        total_called_roulement += share_roulement
        fund_calls_owner.append({
            "date": fc.get("date", ""),
            "name": fc.get("name", ""),
            "call_type": fc.get("call_type", "provisions"),
            "due_date": fc.get("due_date", ""),
            "amount": amt,
            "provisions": share_provisions,
            "reserve": share_reserve,
            "roulement": share_roulement,
            "paid": share.get("paid", False),
            "paid_date": share.get("paid_date", ""),
        })

    total_payments = sum(abs(float(p.get("amount", 0) or 0)) for p in payments)

    # iter90i9 : override total_called et total_payments depuis le grand
    # livre canonique du proprietaire (meme source que la Situation de
    # compte). Assure l'egalite Decompte.header == Situation.total_called
    # meme quand fund_calls.distribution pointe vers l'ancien proprietaire
    # suite a une mutation. Le detail par type d'appel (provisions /
    # reserve / roulement) reste calcule via fund_calls.distribution pour
    # l'affichage de la section 3.
    if owner_ledger_entries:
        # tier accounts explicites du proprietaire pour cette ACP
        _tier_accs = (owner.get("tier_accounts") or {}).get(copropriete.get("id", ""), {}) or {}
        _valid_accs = {a for a in _tier_accs.values() if isinstance(a, str) and a}
        _oid = owner.get("id", "")
        _fy_start = fiscal_year.get("start_date", "")
        _fy_end = fiscal_year.get("end_date", "")
        _debit_ledger = 0.0
        _credit_ledger = 0.0
        seen_lines = set()
        for e in owner_ledger_entries:
            if e.get("reversed") or e.get("is_reversal"):
                continue
            e_date = e.get("date", "")
            if _fy_start and e_date < _fy_start:
                continue
            if _fy_end and e_date > _fy_end:
                continue
            for idx, ln in enumerate(e.get("lines", []) or []):
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                # Accepte les lignes marquees par third_party_id OU par
                # compte tier canonique du proprio (couvre les JE legacy
                # sans third_party_id sur les lignes).
                if tpid != _oid and acc not in _valid_accs:
                    continue
                key = (e.get("id"), idx)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                _debit_ledger += float(ln.get("debit", 0) or 0)
                _credit_ledger += float(ln.get("credit", 0) or 0)
        total_called = round(_debit_ledger, 2)
        total_payments = round(_credit_ledger, 2)

    # iter90g6 : agrege les ecritures de mutation (OD MUT-R / MUT-P / MUT-F)
    # touchant le compte tier du proprietaire. Un ACHETEUR mid-year recoit
    # un DEBIT (dette reprise du vendeur). Un VENDEUR recoit un CREDIT
    # (dette cedee a l'acheteur, remboursable). Sans ce calcul, le solde
    # du decompte ignore ces transferts et affiche un ecart avec Optipro.
    mutation_entries_owner = []
    mutation_net_debit = 0.0  # positif = dettes reprises (a payer)
    for me in (mutation_entries or []):
        if me.get("is_reversal") or me.get("reversed"):
            continue
        for ln in me.get("lines", []) or []:
            if ln.get("third_party_id") != owner.get("id"):
                continue
            d = float(ln.get("debit", 0) or 0)
            c = float(ln.get("credit", 0) or 0)
            net = d - c
            if abs(net) < 0.001:
                continue
            subtype = me.get("source_subtype", "") or ""
            _labels = {
                "fonds_roulement": "Transfert fonds de roulement",
                "prorata": "Prorata appel en cours (mutation)",
                "future_call": "Reprise appel futur (mutation)",
            }
            label = _labels.get(subtype, "Transfert de mutation")
            mutation_entries_owner.append({
                "date": me.get("date", ""),
                "label": label,
                "description": me.get("description", "") or "",
                "reference": me.get("reference", "") or "",
                "amount": net,  # positif = debit (a payer), negatif = credit
                "subtype": subtype,
            })
            mutation_net_debit += net
    mutation_net_debit = round(mutation_net_debit, 2)

    # ---- CALCUL DU SOLDE COMPTABLE CORRECT ----
    # Total IMPUTE DEFINITIVEMENT au proprietaire pour cet exercice :
    #   = Charges reelles reparties (compte 6xx)
    #     + Quote-part fonds de reserve appelee (compte 13X - non remboursable)
    #     + Quote-part fonds de roulement appelee (compte 13X - non remboursable)
    #     + Solde net des ecritures de mutation (iter90g6, ex: dettes reprises)
    # Le BONI/MALI sur provisions = provisions appelees - charges reelles.
    # Solde net pour le proprietaire = Versements - Total impute definitivement
    #   > 0  : EN VOTRE FAVEUR (excedent versement, remboursable ou reportable)
    #   < 0  : RESTE A REGLER (somme due au syndic)
    total_imputed = round(
        total_owner_charges + total_called_reserve + total_called_roulement
        + mutation_net_debit,
        2,
    )
    boni_provisions = round(total_called_provisions - total_owner_charges, 2)
    # iter90i9 : quand le grand livre du proprietaire est fourni (source
    # canonique), on utilise la formule Situation `total_called - total_payments`.
    # Cela evite toute divergence entre Decompte et Situation. Sinon, on
    # tombe sur l'ancien calcul base sur fund_calls (peut ecarter les
    # appels post-mutation pour un acheteur mid-year).
    if owner_ledger_entries:
        balance = round(total_called - total_payments, 2)
    else:
        balance = round(total_imputed - total_payments, 2)

    # ---- SUMMARY CARD ----
    sold_color = RED if balance > 0.01 else (GREEN if balance < -0.01 else SLATE_500)
    sold_label = "RESTE A REGLER" if balance > 0.01 else (
        "EN VOTRE FAVEUR" if balance < -0.01 else "EQUILIBRE"
    )
    bg_summary = RED_BG if balance > 0.01 else (GREEN_BG if balance < -0.01 else SLATE_50)

    summary_rows = [
        [Paragraph("<b>Vos charges sur la periode</b>", body),
         Paragraph(_fmt_eur(total_owner_charges), right)],
        [Paragraph("<b>Total appele par le syndic</b>", body),
         Paragraph(_fmt_eur(total_called), right)],
        [Paragraph("<b>Vos versements pendant la periode</b>", body),
         Paragraph(
             f"<font color='{GREEN.hexval()}'>{_fmt_eur(total_payments)}</font>", right)],
        [Paragraph(f"<b><font size='12'>{sold_label}</font></b>", body),
         Paragraph(
             f"<b><font size='16' color='{sold_color.hexval()}'>{_fmt_eur(abs(balance))}</font></b>",
             right)],
    ]
    summary_tbl = Table(summary_rows, colWidths=[110 * mm, 70 * mm])
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_summary),
        ("BOX", (0, 0), (-1, -1), 1.2, sold_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEABOVE", (0, -1), (-1, -1), 1, sold_color),
        ("TOPPADDING", (0, -1), (-1, -1), 11),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 11),
    ]))
    elems.append(summary_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- 1. DETAIL DES CHARGES (Lot -> Cle de repartition -> Compte) ----
    elems.append(Paragraph("1. Detail de vos charges", h2))
    elems.append(Paragraph(
        "Les depenses de la copropriete sont reparties selon les cles applicables. "
        "Pour chaque cle, le detail est donne par nature de depense, avec la part "
        "refacturable a l'occupant (locataire) et la part definitive du proprietaire.",
        sub_style,
    ))
    elems.append(Spacer(1, 3 * mm))

    dk_by_id = {dk["id"]: dk for dk in distribution_keys}
    lot_by_id = {l["id"]: l for l in owner_lots}
    acc_names = expense_accounts_map or {}

    # 5-column structure (Finlead-style)
    # Designation (90) | Quotites (28) | Montant a repartir (24) | Part proprietaire (19) | Part occupant (19)
    col_widths = [90 * mm, 28 * mm, 24 * mm, 19 * mm, 19 * mm]

    designation_style = ParagraphStyle(
        "desig", parent=body, fontSize=8, leading=10, wordWrap="CJK",
    )
    designation_indent_style = ParagraphStyle(
        "desig_in", parent=designation_style, leftIndent=12,
        textColor=SLATE_500,
    )

    if not per_lot_data:
        elems.append(Paragraph(
            "<i>Aucune charge ne vous concerne sur cette periode.</i>", body))
    else:
        rows = []
        styles_ops = []  # list of (row_index, op_tuple)

        # Header
        rows.append([
            Paragraph("<b>Designation</b>", designation_style),
            Paragraph("<b>Quotites</b>", ParagraphStyle("h", parent=designation_style, alignment=2)),
            Paragraph("<b>Montant<br/>a repartir</b>", ParagraphStyle("h", parent=designation_style, alignment=2)),
            Paragraph("<b>Part<br/>proprietaire</b>", ParagraphStyle("h", parent=designation_style, alignment=2)),
            Paragraph("<b>Part<br/>occupant</b>", ParagraphStyle("h", parent=designation_style, alignment=2)),
        ])
        header_idx = 0

        # Iterate lots in numeric order
        lot_grand_totals = []  # for "Totaux generaux" recap

        for lot_id in sorted(per_lot_data.keys(),
                             key=lambda lid: (lot_by_id.get(lid, {}).get("number", "") or "")):
            by_key = per_lot_data[lot_id]
            lot = lot_by_id.get(lot_id, {"number": "?", "description": ""})

            # Lot header line with dynamic prorata (iter90el)
            _period = lot_owned_period.get(lot_id)
            if _period:
                _, _, _days_owned, _days_total = _period
                _prorata_str = f"{_days_owned} / {_days_total} jours"
            else:
                _prorata_str = f"{fy_days_total or 365} / {fy_days_total or 365} jours"
            lot_label = (
                f"<b>Lot: {lot.get('number','')}"
                + (f" {lot.get('description','')}" if lot.get('description') else "")
                + f"</b> (Prorata: {_prorata_str})"
            )
            rows.append([Paragraph(lot_label, designation_style), "", "", "", ""])
            lot_header_idx = len(rows) - 1
            styles_ops.append(("SPAN", (0, lot_header_idx), (-1, lot_header_idx)))
            styles_ops.append(("BACKGROUND", (0, lot_header_idx), (-1, lot_header_idx), SLATE_100))
            styles_ops.append(("LINEABOVE", (0, lot_header_idx), (-1, lot_header_idx), 0.6, SLATE_300))

            lot_total_dist = 0.0
            lot_total_prop = 0.0
            lot_total_occ = 0.0
            lot_total_owner = 0.0
            # For each key, sorted by name
            for key_id in sorted(by_key.keys(),
                                 key=lambda k: (dk_by_id.get(k, {}).get("name", "") or "")):
                key_data = by_key[key_id]
                accounts = key_data["accounts"]
                if not accounts:
                    continue

                dk_meta = dk_by_id.get(key_id, {})
                if key_id == "_default_tantiemes":
                    dk_name = "Tantiemes (par defaut)"
                elif key_id == "_none":
                    dk_name = "Tantiemes"
                else:
                    dk_name = dk_meta.get("name", "Tantiemes")
                dk_idx = dk_index.get(key_id, {})
                dk_total_q = dk_idx.get("total", 0)
                # Lot quotity in this key (fallback to lot.quotity if not configured)
                lot_q = dk_idx.get("lots", {}).get(lot_id)
                if not lot_q:  # None or 0 -> try fallback
                    fallback_q = float(lot.get("quotity", 0) or 0)
                    if fallback_q:
                        lot_q = fallback_q
                if lot_q is None:
                    lot_q = 0.0
                if not dk_total_q:
                    dk_total_q = sum(float(l.get("quotity", 0) or 0) for l in all_lots) or lot_q

                quot_str = f"{lot_q:.2f} / {dk_total_q:.2f}".replace(",", " ")

                # Key totals across accounts
                key_total_dist = sum(a["total_dist"] for a in accounts.values())
                key_total_amt = sum(a["owner_amt"] for a in accounts.values())
                key_total_occ = sum(a["owner_occ"] for a in accounts.values())
                key_total_prop = sum(a["owner_prop"] for a in accounts.values())

                # Key header row (bold, with totals)
                # Format: "0001 - Quotites (10000.000)"
                code = dk_meta.get("code") or dk_meta.get("number") or ""
                key_designation = (
                    f"<b>{code + ' - ' if code else ''}{dk_name}"
                    + (f" ({dk_total_q:.3f})" if dk_total_q else "")
                    + "</b>"
                )
                rows.append([
                    Paragraph(key_designation, designation_style),
                    Paragraph(quot_str, ParagraphStyle("q", parent=designation_style, alignment=2)),
                    Paragraph(_fmt_eur(key_total_dist), ParagraphStyle("q", parent=designation_style, alignment=2)),
                    Paragraph(f"<b>{_fmt_eur(key_total_prop)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#1E40AF"))),
                    Paragraph(f"<b>{_fmt_eur(key_total_occ)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#92400E"))),
                ])
                key_idx = len(rows) - 1
                styles_ops.append(("BACKGROUND", (0, key_idx), (-1, key_idx), colors.HexColor("#EFF6FF")))
                styles_ops.append(("LINEBELOW", (0, key_idx), (-1, key_idx), 0.3, SLATE_300))

                # Detail rows per account
                for acc in sorted(accounts.keys()):
                    a = accounts[acc]
                    nature_label = acc_names.get(acc, "") or ("Autres charges" if acc == "_other" else acc)
                    acc_display = f"{acc} - {nature_label}" if acc != "_other" else nature_label
                    # Account aggregated row (bold-ish, slate)
                    rows.append([
                        Paragraph(f"<b>{acc_display}</b>", designation_indent_style),
                        Paragraph(quot_str, ParagraphStyle("q", parent=designation_style, alignment=2, textColor=SLATE_500)),
                        Paragraph(_fmt_eur(a["total_dist"]), ParagraphStyle("q", parent=designation_style, alignment=2, textColor=SLATE_500)),
                        Paragraph(f"<b>{_fmt_eur(a['owner_prop'])}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#1E40AF"))),
                        Paragraph(f"<b>{_fmt_eur(a['owner_occ'])}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#92400E"))),
                    ])
                    acc_row_idx = len(rows) - 1
                    styles_ops.append(("BACKGROUND", (0, acc_row_idx), (-1, acc_row_idx), colors.HexColor("#F8FAFC")))

                    # Invoice-level detail lines (sorted by date)
                    sorted_invs = sorted(a.get("invoices", []),
                                         key=lambda iv: (iv.get("date") or ""))
                    for iv in sorted_invs:
                        date_str = _fmt_date(iv.get("date", ""))
                        supplier = iv.get("supplier", "") or "-"
                        descr = iv.get("description", "") or ""
                        ref = iv.get("reference", "") or ""
                        # First column: "JJ/MM/AAAA - Supplier — description (ref FA-...)"
                        parts = [f"<font color='{SLATE_500.hexval()}'>{date_str}</font>"]
                        parts.append(supplier)
                        if descr:
                            parts.append(f"<font color='{SLATE_500.hexval()}'>- {descr}</font>")
                        if ref:
                            parts.append(f"<font color='{SLATE_500.hexval()}' size='7'>({ref})</font>")
                        if iv.get("auto_split"):
                            parts.append(
                                f"<font color='#D97706' size='7'><i>repartition auto (tantiemes)</i></font>"
                            )
                        # iter90g5 : le prorata mutation est deja affiche UNE
                        # FOIS dans l'entete du lot ("Prorata: 318/365 jours").
                        # Retire du detail par ligne pour alleger la lecture
                        # (demande utilisateur - meme convention qu'Optipro).
                        # Le calcul reste applique sur les montants.
                        inv_label = " ".join(parts)
                        inv_indent_style = ParagraphStyle(
                            "inv_in", parent=designation_style,
                            leftIndent=24, fontSize=7.5, leading=9.5,
                        )
                        rows.append([
                            Paragraph(inv_label, inv_indent_style),
                            "",  # quotites not relevant per-invoice
                            Paragraph(_fmt_eur(iv["total_amount"]),
                                      ParagraphStyle("q", parent=designation_style, alignment=2, fontSize=7.5, textColor=SLATE_500)),
                            Paragraph(_fmt_eur(iv["owner_prop"]) if iv["owner_prop"] > 0.001 else "-",
                                      ParagraphStyle("q", parent=designation_style, alignment=2, fontSize=7.5, textColor=colors.HexColor("#1E40AF"))),
                            Paragraph(_fmt_eur(iv["owner_occ"]) if iv["owner_occ"] > 0.001 else "-",
                                      ParagraphStyle("q", parent=designation_style, alignment=2, fontSize=7.5, textColor=colors.HexColor("#92400E"))),
                        ])

                    # Track occupant summary label
                    if a["owner_occ"] > 0.001 and acc in occupant_summary_by_acc:
                        occupant_summary_by_acc[acc]["label"] = nature_label

                lot_total_owner += key_total_amt
                lot_total_occ += key_total_occ
                lot_total_prop += key_total_prop

            # iter90fz : lot_total_dist doit refleter la somme des invoices
            # UNIQUES ayant touche ce lot (dedup par inv_id), pas la somme
            # des `key_total_dist` qui multi-comptent quand un meme invoice
            # touche plusieurs comptes.
            lot_total_dist = lot_dist_unique.get(lot_id, 0.0)

            # Lot subtotal row
            lot_subtotal_label = f"<b>Total Lot {lot.get('number','')}</b>"
            rows.append([
                Paragraph(lot_subtotal_label, designation_style),
                "",
                Paragraph(f"<b>{_fmt_eur(lot_total_dist)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2)),
                Paragraph(f"<b>{_fmt_eur(lot_total_prop)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#1E40AF"))),
                Paragraph(f"<b>{_fmt_eur(lot_total_occ)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.HexColor("#92400E"))),
            ])
            sub_idx = len(rows) - 1
            styles_ops.append(("BACKGROUND", (0, sub_idx), (-1, sub_idx), SLATE_100))
            styles_ops.append(("LINEABOVE", (0, sub_idx), (-1, sub_idx), 0.6, SLATE_500))
            styles_ops.append(("LINEBELOW", (0, sub_idx), (-1, sub_idx), 0.6, SLATE_500))

            lot_grand_totals.append((lot, lot_total_dist, lot_total_prop, lot_total_occ))

        # Totaux generaux row
        # iter90fz : grand_dist utilise la somme UNIQUE des invoices vues
        # au niveau owner (dedup par inv_id), pas la somme des lot_totals
        # qui compterait N fois un invoice touchant N lots.
        grand_dist = owner_grand_dist_unique
        grand_prop = sum(t[2] for t in lot_grand_totals)
        grand_occ = sum(t[3] for t in lot_grand_totals)

        rows.append([
            Paragraph("<b>Totaux generaux</b>", designation_style),
            "",
            Paragraph(f"<b>{_fmt_eur(grand_dist)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.white)),
            Paragraph(f"<b>{_fmt_eur(grand_prop)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.white)),
            Paragraph(f"<b>{_fmt_eur(grand_occ)}</b>", ParagraphStyle("q", parent=designation_style, alignment=2, textColor=colors.white)),
        ])
        grand_idx = len(rows) - 1
        styles_ops.append(("BACKGROUND", (0, grand_idx), (-1, grand_idx), SLATE_900))
        styles_ops.append(("TEXTCOLOR", (0, grand_idx), (-1, grand_idx), colors.white))

        # Build the main table
        main_tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        base_style = [
            ("BACKGROUND", (0, header_idx), (-1, header_idx), BRAND),
            ("TEXTCOLOR", (0, header_idx), (-1, header_idx), colors.white),
            ("FONTNAME", (0, header_idx), (-1, header_idx), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("BOX", (0, 0), (-1, -1), 0.4, SLATE_300),
        ]
        for op in styles_ops:
            base_style.append(op)
        main_tbl.setStyle(TableStyle(base_style))
        elems.append(main_tbl)

    elems.append(Spacer(1, 6 * mm))

    # ---- 2. RECAPITULATIF CHARGES LOCATAIRE (SECTION DEDIEE) ----
    if total_occupant_share > 0.001:
        elems.append(Paragraph("2. Recapitulatif des charges locataire", h2))
        elems.append(Paragraph(
            "Synthese des charges refacturables a votre locataire (parts \"occupant\"), "
            "detaillees par nature de depense. Ce recapitulatif vous permet d'etablir "
            "le decompte annuel des charges locatives.",
            sub_style,
        ))
        elems.append(Spacer(1, 3 * mm))

        rec_rows = [[
            Paragraph("<b>Nature de la depense</b>", designation_style),
            Paragraph("<b>Compte</b>", ParagraphStyle("h", parent=designation_style, alignment=1)),
            Paragraph("<b>Montant a refacturer<br/>au locataire</b>", ParagraphStyle("h", parent=designation_style, alignment=2)),
        ]]
        # Sort by amount desc
        sorted_occ = sorted(
            occupant_summary_by_acc.items(),
            key=lambda kv: kv[1]["amount"],
            reverse=True,
        )
        for acc, info in sorted_occ:
            if info["amount"] <= 0.001:
                continue
            label = info.get("label") or acc_names.get(acc, "") or (
                "Autres charges" if acc == "_other" else acc
            )
            acc_display = acc if acc != "_other" else "—"
            rec_rows.append([
                Paragraph(label, designation_style),
                Paragraph(
                    f"<font name='Courier' size='8'>{acc_display}</font>",
                    ParagraphStyle("c", parent=designation_style, alignment=1, textColor=SLATE_500),
                ),
                Paragraph(
                    f"<b>{_fmt_eur(info['amount'])}</b>",
                    ParagraphStyle("a", parent=designation_style, alignment=2, textColor=colors.HexColor("#92400E")),
                ),
            ])
        rec_rows.append([
            Paragraph("<b>TOTAL A REFACTURER AU LOCATAIRE</b>", designation_style),
            "",
            Paragraph(
                f"<b><font size='11'>{_fmt_eur(total_occupant_share)}</font></b>",
                ParagraphStyle("a", parent=designation_style, alignment=2, textColor=colors.white),
            ),
        ])

        rec_tbl = Table(rec_rows, colWidths=[120 * mm, 25 * mm, 35 * mm])
        rec_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#92400E")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#92400E")),
            ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, colors.HexColor("#FEF3C7")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#92400E")),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, SLATE_300),
        ]))
        elems.append(rec_tbl)
        # Quick note
        elems.append(Spacer(1, 2 * mm))
        elems.append(Paragraph(
            "<font size='7.5' color='#64748B'><i>Repartition occupant/proprietaire definie "
            "par nature de depense conformement aux usages locatifs belges (RD du 12/07/2024 "
            "relatif aux charges locatives). A confronter, le cas echeant, avec les "
            "stipulations particulieres du bail.</i></font>",
            small,
        ))
        elems.append(Spacer(1, 6 * mm))

    # ---- 3. APPELS DE FONDS DE RESERVE / ROULEMENT ----
    # Les appels de provisions sont absorbes par les charges reelles (section 1).
    # Seuls les appels de fonds de reserve et fonds de roulement sont affiches ici,
    # car ils representent des contributions DEFINITIVES (non remboursables)
    # alimentant les fonds permanents de la copropriete.
    fund_calls_capital = [
        fc for fc in fund_calls_owner
        if (fc.get("reserve", 0) or 0) > 0.001
        or (fc.get("roulement", 0) or 0) > 0.001
    ]
    elems.append(Paragraph("3. Vos appels de fonds de reserve et de roulement", h2))
    elems.append(Paragraph(
        "Contributions definitives aux fonds permanents de la copropriete "
        "(non remboursables). Les appels de provisions pour charges sont quant "
        "a eux directement consommes par vos charges reelles (cf. section 1).",
        sub_style,
    ))
    elems.append(Spacer(1, 2 * mm))

    if not fund_calls_capital:
        elems.append(Paragraph(
            "<i>Aucun appel de fonds de reserve ou de roulement sur cette periode.</i>", body))
    else:
        total_reserve = sum((fc.get("reserve", 0) or 0) for fc in fund_calls_capital)
        total_roulement = sum((fc.get("roulement", 0) or 0) for fc in fund_calls_capital)
        total_capital = total_reserve + total_roulement
        fc_rows = [["Date", "Libelle", "Type", "Echeance", "Montant", "Statut"]]
        fc_cell = ParagraphStyle(
            "fc", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
        )
        fc_type_style = ParagraphStyle(
            "fct", parent=fc_cell, alignment=0,
        )
        for fc in fund_calls_capital:
            reserve = fc.get("reserve", 0) or 0
            roulement = fc.get("roulement", 0) or 0
            statut = ("Paye " + _fmt_date(fc["paid_date"])) if fc["paid"] else "A payer"
            # Une ligne par type pour clarte
            if reserve > 0.001:
                fc_rows.append([
                    _fmt_date(fc["date"]),
                    Paragraph(fc["name"] or "", fc_cell),
                    Paragraph(
                        "<font color='#1E40AF'><b>Fonds de reserve</b></font>",
                        fc_type_style,
                    ),
                    _fmt_date(fc["due_date"]),
                    _fmt_eur(reserve),
                    statut,
                ])
            if roulement > 0.001:
                fc_rows.append([
                    _fmt_date(fc["date"]),
                    Paragraph(fc["name"] or "", fc_cell),
                    Paragraph(
                        "<font color='#92400E'><b>Fonds de roulement</b></font>",
                        fc_type_style,
                    ),
                    _fmt_date(fc["due_date"]),
                    _fmt_eur(roulement),
                    statut,
                ])
        # Sous-totaux par type
        fc_rows.append([
            "", "",
            Paragraph(
                "<font color='#1E40AF'><b>Sous-total reserve</b></font>",
                fc_type_style,
            ),
            "", _fmt_eur(total_reserve), "",
        ])
        fc_rows.append([
            "", "",
            Paragraph(
                "<font color='#92400E'><b>Sous-total roulement</b></font>",
                fc_type_style,
            ),
            "", _fmt_eur(total_roulement), "",
        ])
        fc_rows.append(["", "", "", "TOTAL", _fmt_eur(total_capital), ""])

        fc_tbl = Table(fc_rows, colWidths=[20 * mm, 50 * mm, 36 * mm, 22 * mm, 26 * mm, 20 * mm])
        fc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("ALIGN", (5, 0), (5, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -4),
             [colors.white, SLATE_50]),
            # 2 sous-totaux + 1 total = 3 lignes a styliser
            ("BACKGROUND", (0, -3), (-1, -2), colors.HexColor("#F8FAFC")),
            ("LINEABOVE", (0, -3), (-1, -3), 0.5, SLATE_300),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (3, -1), (4, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (4, -1), (4, -1), BRAND),
            ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(fc_tbl)

    elems.append(Spacer(1, 8 * mm))

    # ---- 4. TRANSFERTS DE MUTATION (iter90g6) ----
    # Detail des OD source_type=lot_mutation touchant le compte tier de
    # l'owner : MUT-R (fonds de roulement transfere), MUT-P (prorata appel
    # en cours), MUT-F (reprise appels futurs). Un ACHETEUR voit ces
    # montants comme des DEBITS (dettes reprises du vendeur, a payer). Un
    # VENDEUR les voit comme des CREDITS (dettes cedees, remboursables).
    if mutation_entries_owner:
        elems.append(Paragraph(
            "3bis. Transferts lies a la mutation",
            h2,
        ))
        elems.append(Paragraph(
            "Ecritures generees lors du transfert de propriete (fonds de "
            "roulement, prorata des appels en cours). Un DEBIT augmente ce "
            "que vous devez au syndic (dettes reprises du vendeur). Un "
            "CREDIT diminue votre solde (dettes cedees a l'acheteur).",
            sub_style,
        ))
        elems.append(Spacer(1, 2 * mm))
        mut_rows = [["Date", "Libelle", "Debit", "Credit"]]
        mut_cell = ParagraphStyle(
            "mut", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
        )
        total_mut_debit_disp = 0.0
        total_mut_credit_disp = 0.0
        for me in sorted(mutation_entries_owner, key=lambda x: x.get("date", "")):
            amt = float(me.get("amount", 0) or 0)
            d_amt = amt if amt > 0 else 0.0
            c_amt = -amt if amt < 0 else 0.0
            total_mut_debit_disp += d_amt
            total_mut_credit_disp += c_amt
            mut_rows.append([
                _fmt_date(me.get("date", "")),
                Paragraph(me.get("label", "") + (
                    f" <font color='#94A3B8' size='7'>({me.get('reference','')})</font>"
                    if me.get("reference") else ""
                ), mut_cell),
                _fmt_eur(d_amt) if d_amt > 0.001 else "-",
                _fmt_eur(c_amt) if c_amt > 0.001 else "-",
            ])
        mut_rows.append([
            "",
            Paragraph("<b>TOTAL</b>", mut_cell),
            _fmt_eur(total_mut_debit_disp) if total_mut_debit_disp > 0.001 else "-",
            _fmt_eur(total_mut_credit_disp) if total_mut_credit_disp > 0.001 else "-",
        ])
        mut_tbl = Table(mut_rows, colWidths=[22 * mm, 110 * mm, 20 * mm, 20 * mm])
        mut_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (2, 0), (3, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (1, -1), (3, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(mut_tbl)
        elems.append(Spacer(1, 8 * mm))

    # ---- 4. PAIEMENTS ----
    if payments:
        elems.append(Paragraph("4. Vos paiements", h2))
        elems.append(Paragraph(
            "Les versements recus par la copropriete et associes a votre compte.",
            sub_style,
        ))
        elems.append(Spacer(1, 2 * mm))
        pay_rows = [["Date", "Communication", "Montant"]]
        pay_cell = ParagraphStyle(
            "pay", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
        )
        for p in payments:
            amt = abs(float(p.get("amount", 0) or 0))
            pay_rows.append([
                _fmt_date(p.get("date", "")),
                Paragraph(p.get("communication", "") or p.get("counterparty_name", "") or "", pay_cell),
                _fmt_eur(amt),
            ])
        pay_rows.append(["", "TOTAL VERSE", _fmt_eur(total_payments)])
        pay_tbl = Table(pay_rows, colWidths=[22 * mm, 122 * mm, 26 * mm])
        pay_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (1, -1), (2, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (2, -1), (2, -1), GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GREEN),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(pay_tbl)
        elems.append(Spacer(1, 8 * mm))

    # ---- 5. MODALITES DE PAIEMENT ----
    iban = ""
    bic = ""
    for ba in copropriete.get("bank_accounts", []) or []:
        if ba.get("iban"):
            iban = ba["iban"]
            bic = ba.get("bic", "")
            break

    if balance > 0.01:
        rib_lines = [
            "<font size='11'><b>Comment regler ce solde ?</b></font>",
            "&nbsp;",
            f"Veuillez verser <b>{_fmt_eur(balance)}</b> sur le compte :",
            f"<b>IBAN :</b> <font name='Courier'>{iban or '—'}</font>",
            f"<b>BIC :</b> <font name='Courier'>{bic or '—'}</font>",
            f"<b>Beneficiaire :</b> {copropriete.get('name','')}",
            "&nbsp;",
            "<b>Communication structuree (obligatoire) :</b>",
            f"<font color='{BRAND.hexval()}' size='13'><b>{owner.get('vcs_code','')}</b></font>",
            "&nbsp;",
            "<font size='8' color='#64748B'><i>Indiquez imperativement cette communication "
            "structuree afin que votre paiement soit reconnu automatiquement et impute "
            "sur votre compte.</i></font>",
        ]
        rib = Paragraph("<br/>".join(rib_lines), body)
        rib_tbl = Table([[rib]], colWidths=[180 * mm])
        rib_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#3B82F6")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        elems.append(KeepTogether(rib_tbl))
        elems.append(Spacer(1, 6 * mm))
    elif balance < -0.01:
        msg = Paragraph(
            f"<font size='11'><b>Solde en votre faveur : {_fmt_eur(abs(balance))}</b></font><br/>"
            "&nbsp;<br/>"
            "Ce solde sera deduit de votre prochain appel de fonds. "
            "Pour obtenir le remboursement par virement, transmettez votre IBAN au syndic.",
            body,
        )
        msg_tbl = Table([[msg]], colWidths=[180 * mm])
        msg_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
            ("BOX", (0, 0), (-1, -1), 1, GREEN),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        elems.append(msg_tbl)
        elems.append(Spacer(1, 6 * mm))

    # ---- FOOTER ----
    elems.append(Spacer(1, 4 * mm))
    elems.append(Paragraph(
        f"<font size='7.5' color='#94A3B8'>"
        f"<i>Decompte etabli conformement a la loi belge sur la copropriete "
        f"(art. 3.86 et suivants du Code civil). Toute contestation doit etre formulee "
        f"par ecrit au syndic dans les 30 jours suivant reception.</i>"
        f"<br/>Document edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')} - "
        f"{owner.get('name','')} - {copropriete.get('name','')}.</font>",
        small,
    ))

    def _combined_cb(canvas, doc):
        # Watermark preview
        _make_watermark(preview)(canvas, doc)
        # Footer legal si new layout
        if use_new_layout:
            draw_legal_footer(canvas, doc, syndic_pdf_ctx.get("legal_mentions", ""))
    doc.build(elems, onFirstPage=_combined_cb, onLaterPages=_combined_cb)
    buf.seek(0)
    return buf.read()


def _make_watermark(preview: bool):
    """Return a canvas callback that draws an APERCU watermark when preview=True."""
    if not preview:
        return lambda canvas, doc: None

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.translate(105 * mm, 148 * mm)  # center A4
        canvas.rotate(45)
        canvas.setFont("Helvetica-Bold", 70)
        canvas.setFillColorRGB(0.85, 0.10, 0.10, alpha=0.18)
        canvas.drawCentredString(0, 0, "APERCU")
        canvas.setFont("Helvetica-Bold", 18)
        canvas.setFillColorRGB(0.85, 0.10, 0.10, alpha=0.35)
        canvas.drawCentredString(0, -45, "NON DEFINITIF")
        canvas.restoreState()
        # Banner top-right
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColorRGB(0.85, 0.10, 0.10)
        canvas.drawRightString(200 * mm, 287 * mm,
                               "APERCU - Document non definitif")
        canvas.restoreState()

    return _draw
