"""iter93dt - Facturation plateforme (superadmin).

Endpoint permettant au superadmin de facturer les syndics selon un bareme
degressif par tranche + option "negocie" (forfait fixe).

Regles :
  - Tranches saisies par le superadmin : (min_lots, max_lots, price_per_lot).
    Prix DEGRESSIF : lots 1..N calcules par la tranche ou N tombe.
    Exemple : tranches [1-50 @ 5€], [51-200 @ 4€], [201+ @ 3€]
      -> 250 lots = 50*5 + 150*4 + 50*3 = 250 + 600 + 150 = 1000 EUR
  - Forfait negocie (`negotiated_flat_fee` par syndic) : ecrase le calcul.
  - Frequence : mensuel / trimestriel / annuel (defaut annuel).
  - Comptage : lots ACTIFS uniquement (copropriete.status != archived).

Collections :
  - `billing_config` : singleton { id: 'default', tiers, vat_rate, default_frequency, admin_info }
  - `syndic_billing` : par syndic { syndic_user_id, frequency, negotiated_flat_fee, notes }
"""
import io
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("billing_admin")


class Tier(BaseModel):
    min_lots: int
    max_lots: Optional[int] = None  # None = pas de limite haute
    annual_fee: float = 0.0  # forfait annuel FIXE ajoute au debut de la tranche
    marginal_per_lot: float = 0.0  # EUR/lot/mois marginal DANS cette tranche
    # NOTE: le calcul cumulatif est active des qu'AU MOINS UNE tranche a
    # marginal_per_lot > 0. Sinon on garde le mode "forfait fixe par tranche".


class BillingConfig(BaseModel):
    tiers: List[Tier] = Field(default_factory=list)
    vat_rate: float = 21.0
    default_frequency: str = "annual"  # monthly | quarterly | annual
    admin_info: dict = Field(default_factory=dict)  # {name, address, iban, vat_number, logo_url}


class SyndicBillingUpdate(BaseModel):
    frequency: Optional[str] = None
    negotiated_flat_fee: Optional[float] = None
    notes: Optional[str] = None


def _apply_frequency_multiplier(annual_amount: float, frequency: str) -> float:
    """annual_amount is what the syndic pays PER YEAR. Convert to per-period."""
    if frequency == "monthly":
        return annual_amount / 12.0
    if frequency == "quarterly":
        return annual_amount / 4.0
    return annual_amount  # annual


def _compute_tiered_amount(lots: int, tiers: List[dict]) -> float:
    """Calcule le montant ANNUEL total pour un nombre de lots.

    Deux modes :
    1) FORFAIT FIXE par tranche : chaque tranche a un `annual_fee` unique
       (pas de prix par lot). On renvoie le forfait de la tranche contenant `lots`.
    2) CUMULATIF MARGINAL (2026-02) : chaque tranche peut definir
       `marginal_per_lot` (EUR/lot/mois). Le montant est calcule en
       accumulant tranche par tranche jusqu'a `lots`. Active des qu'AU
       MOINS UNE tranche a `marginal_per_lot > 0`.

    Exemple bareme belge standard :
      - Base 1-50    : 99 EUR/mois (forfait, marginal=0)
      - 51-150       : +0.60 EUR/lot/mois pour lots 51..150
      - 151-500      : +0.24 EUR/lot/mois pour lots 151..500
      - etc.

    Retour : montant ANNUEL en EUR (converti via *12 pour marginal_per_lot).
    """
    if lots <= 0 or not tiers:
        return 0.0
    sorted_tiers = sorted(tiers, key=lambda t: t.get("min_lots", 0))
    has_marginal = any(float(t.get("marginal_per_lot") or 0) > 0 for t in sorted_tiers)

    if has_marginal:
        # Mode CUMULATIF : on accumule tranche par tranche
        monthly_total = 0.0
        annual_add = 0.0
        for t in sorted_tiers:
            lo = int(t.get("min_lots", 0))
            hi = t.get("max_lots")
            hi = int(hi) if hi is not None else 10**9
            if lots < lo:
                break
            # Portion fixe : forfait de tranche (converti mensuel dans le
            # cadre du bareme belge : base = 99 EUR/mois, donc annual_fee=1188).
            # On considere annual_fee comme un montant ANNUEL fixe additionnel
            # pour cette tranche, ajoute une seule fois.
            annual_add += float(t.get("annual_fee") or 0)
            # Portion marginale : nb de lots dans la tranche * marginal_per_lot * 12
            marginal = float(t.get("marginal_per_lot") or 0)
            if marginal > 0:
                span_lots = min(lots, hi) - lo + 1
                if span_lots > 0:
                    monthly_total += span_lots * marginal
            if lots <= hi:
                break
        return round(annual_add + monthly_total * 12, 2)

    # Mode FORFAIT FIXE (retro-compat)
    for t in sorted_tiers:
        lo = t.get("min_lots", 0)
        hi = t.get("max_lots") if t.get("max_lots") is not None else 10**9
        if lo <= lots <= hi:
            fee = t.get("annual_fee")
            if fee is None:
                legacy = t.get("price_per_lot")
                if legacy is not None:
                    return float(legacy) * lots
                return 0.0
            return round(float(fee), 2)
    return 0.0


def create_billing_admin_router(db):
    router = APIRouter(prefix="/api/admin/billing", tags=["admin-billing"])

    async def _ensure_superadmin(request: Request):
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Reserve au super administrateur")
        return user

    async def _load_config():
        cfg = await db.billing_config.find_one({"id": "default"}, {"_id": 0})
        if not cfg:
            # Bareme par defaut suggere - forfait annuel FIXE par tranche
            cfg = {
                "id": "default",
                "tiers": [
                    {"min_lots": 1, "max_lots": 50, "annual_fee": 500.0},
                    {"min_lots": 51, "max_lots": 200, "annual_fee": 1200.0},
                    {"min_lots": 201, "max_lots": None, "annual_fee": 2500.0},
                ],
                "vat_rate": 21.0,
                "default_frequency": "annual",
                "admin_info": {},
            }
        return cfg

    @router.post("/apply-preset")
    async def apply_belgian_preset(request: Request):
        """Applique le bareme standard belge de facturation syndic
        (base 99 EUR/mois + tranches marginales par lot).
        """
        await _ensure_superadmin(request)
        # Bareme standard 2026 : base fixe + marges cumulatives
        # Note: annual_fee sur la tranche BASE = 99 EUR/mois * 12 = 1188 EUR/an
        # Les autres tranches n'ajoutent QUE du marginal (pas d'annual_fee)
        tiers = [
            {"min_lots": 1,    "max_lots": 50,   "annual_fee": 1188.0, "marginal_per_lot": 0.0},
            {"min_lots": 51,   "max_lots": 150,  "annual_fee": 0.0,    "marginal_per_lot": 0.60},
            {"min_lots": 151,  "max_lots": 500,  "annual_fee": 0.0,    "marginal_per_lot": 0.24},
            {"min_lots": 501,  "max_lots": 1000, "annual_fee": 0.0,    "marginal_per_lot": 0.20},
            {"min_lots": 1001, "max_lots": 2000, "annual_fee": 0.0,    "marginal_per_lot": 0.15},
            {"min_lots": 2001, "max_lots": None, "annual_fee": 0.0,    "marginal_per_lot": 0.10},
        ]
        doc = {
            "id": "default",
            "tiers": tiers,
            "vat_rate": 21.0,
            "default_frequency": "monthly",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.billing_config.update_one(
            {"id": "default"},
            {"$set": doc},
            upsert=True,
        )
        # Verifications : quelques montants de reference
        samples = {n: _compute_tiered_amount(n, tiers) for n in [50, 150, 500, 1000, 2000, 3000]}
        return {"ok": True, "config": doc, "samples_annual_eur": samples}

    @router.get("/simulate")
    async def simulate_fee(lots: int, request: Request):
        """Simule le calcul pour un nombre de lots donne. Utile pour l'UI
        preview du bareme.
        """
        await _ensure_superadmin(request)
        cfg = await _load_config()
        annual = _compute_tiered_amount(lots, cfg.get("tiers", []))
        return {
            "lots": lots,
            "annual_htva": round(annual, 2),
            "monthly_htva": round(annual / 12, 2),
            "annual_tvac": round(annual * (1 + cfg.get("vat_rate", 21) / 100), 2),
        }

    @router.get("/config")
    async def get_config(request: Request):
        await _ensure_superadmin(request)
        return await _load_config()

    @router.put("/config")
    async def update_config(cfg: BillingConfig, request: Request):
        await _ensure_superadmin(request)
        doc = cfg.model_dump()
        doc["id"] = "default"
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.billing_config.update_one({"id": "default"}, {"$set": doc}, upsert=True)
        return {"ok": True, "config": doc}

    @router.get("/syndics")
    async def list_syndics_billing(request: Request):
        """Liste tous les syndics avec :
          - nombre de lots actifs
          - frequence de facturation
          - forfait negocie (si present)
          - montant annuel calcule par le bareme
          - montant de la periode (selon frequency)
        """
        await _ensure_superadmin(request)
        cfg = await _load_config()
        tiers = cfg.get("tiers", [])
        default_freq = cfg.get("default_frequency", "annual")

        # Recuperer tous les users role syndic (comptes 'syndic', pas gestionnaires)
        syndic_users = await db.users.find(
            {"role": "syndic"}, {"password_hash": 0}
        ).to_list(1000)

        # Config billing par syndic
        billing_map = {}
        async for b in db.syndic_billing.find({}, {"_id": 0}):
            billing_map[b.get("syndic_user_id", "")] = b

        # ACPs actives par syndic
        # ACP.syndic_id = str(user._id) ou ACP.syndic_user_id
        acps = await db.coproprietes.find(
            {"status": {"$ne": "archived"}}, {"_id": 0, "id": 1, "syndic_id": 1, "syndic_user_id": 1, "name": 1}
        ).to_list(2000)
        acps_by_syndic: dict = {}
        for a in acps:
            sid = a.get("syndic_id") or a.get("syndic_user_id") or ""
            if sid:
                acps_by_syndic.setdefault(sid, []).append(a)

        # Lots actifs (ACP non-archived) par syndic
        rows = []
        for u in syndic_users:
            uid = str(u.get("_id", ""))
            my_acps = acps_by_syndic.get(uid, [])
            acp_ids = [a["id"] for a in my_acps]
            lot_count = 0
            if acp_ids:
                lot_count = await db.lots.count_documents({"copropriete_id": {"$in": acp_ids}})
            billing = billing_map.get(uid, {})
            freq = billing.get("frequency") or default_freq
            nego = billing.get("negotiated_flat_fee")
            if nego is not None and nego >= 0:
                annual_amount = float(nego)
                pricing_mode = "negocie"
            else:
                annual_amount = _compute_tiered_amount(lot_count, tiers)
                pricing_mode = "tranches"
            period_amount = _apply_frequency_multiplier(annual_amount, freq)
            rows.append({
                "syndic_user_id": uid,
                "syndic_name": u.get("name", "") or u.get("email", ""),
                "syndic_email": u.get("email", ""),
                "acp_count": len(my_acps),
                "acp_names": [a.get("name", "") for a in my_acps][:5],
                "lot_count": lot_count,
                "frequency": freq,
                "negotiated_flat_fee": nego,
                "pricing_mode": pricing_mode,
                "annual_amount_ht": round(annual_amount, 2),
                "period_amount_ht": round(period_amount, 2),
                "vat_rate": cfg.get("vat_rate", 21.0),
                "period_amount_tva": round(period_amount * cfg.get("vat_rate", 21.0) / 100.0, 2),
                "period_amount_ttc": round(period_amount * (1 + cfg.get("vat_rate", 21.0) / 100.0), 2),
                "notes": billing.get("notes", ""),
            })
        rows.sort(key=lambda r: (r["syndic_name"] or "").lower())
        totals = {
            "annual_ht": round(sum(r["annual_amount_ht"] for r in rows), 2),
            "period_ht": round(sum(r["period_amount_ht"] for r in rows), 2),
            "period_ttc": round(sum(r["period_amount_ttc"] for r in rows), 2),
            "lot_count": sum(r["lot_count"] for r in rows),
        }
        return {"rows": rows, "totals": totals, "config": cfg}

    @router.put("/syndics/{syndic_user_id}")
    async def update_syndic_billing(syndic_user_id: str, upd: SyndicBillingUpdate, request: Request):
        await _ensure_superadmin(request)
        # iter93dt-fix : distinguer un champ non-envoye (exclude_unset) d'un
        # champ explicitement mis a null. `null` doit UNSET la valeur en DB
        # (retour au bareme), pas etre ignore.
        payload = upd.model_dump(exclude_unset=True)
        set_doc = {"syndic_user_id": syndic_user_id, "updated_at": datetime.now(timezone.utc).isoformat()}
        unset_doc: dict = {}
        for k, v in payload.items():
            if v is None:
                unset_doc[k] = ""
            else:
                set_doc[k] = v
        update = {"$set": set_doc}
        if unset_doc:
            update["$unset"] = unset_doc
        await db.syndic_billing.update_one(
            {"syndic_user_id": syndic_user_id}, update, upsert=True
        )
        return {"ok": True, "syndic_user_id": syndic_user_id}

    @router.get("/invoice/{syndic_user_id}/pdf")
    async def generate_invoice_pdf(syndic_user_id: str, request: Request, period_label: str = ""):
        """Genere une facture PDF pour un syndic (usage interne du cabinet admin).

        `period_label` : ex. "2026-Q3", "2026-11", "2026" (libre).
        """
        await _ensure_superadmin(request)
        from bson import ObjectId
        try:
            u = await db.users.find_one({"_id": ObjectId(syndic_user_id)}, {"password_hash": 0})
        except Exception:
            u = None
        if not u:
            raise HTTPException(404, "Syndic introuvable")

        # Reutilise la liste pour trouver la ligne
        data = await list_syndics_billing(request)
        row = next((r for r in data["rows"] if r["syndic_user_id"] == syndic_user_id), None)
        if not row:
            raise HTTPException(404, "Aucune ligne de facturation pour ce syndic")

        pdf_bytes = _render_invoice_pdf(row, data["config"], period_label)
        safe_name = (row["syndic_name"] or "syndic").replace(" ", "_")
        fn = f"facture-{safe_name}-{period_label or 'periode'}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fn}"'},
        )

    return router


def _render_invoice_pdf(row: dict, cfg: dict, period_label: str) -> bytes:
    """PDF facture interne (usage cabinet admin -> syndic client)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    styles = getSampleStyleSheet()
    st_title = ParagraphStyle(
        "T", parent=styles["Title"], textColor=colors.HexColor("#022D52"),
        fontSize=22, spaceAfter=8, alignment=0,
    )
    st_h = ParagraphStyle("H", parent=styles["Heading2"], textColor=colors.HexColor("#022D52"),
                          fontSize=11, spaceBefore=10, spaceAfter=4)
    st_normal = styles["BodyText"]

    admin = cfg.get("admin_info", {}) or {}
    admin_name = admin.get("name", "")
    admin_address = admin.get("address", "")
    admin_iban = admin.get("iban", "")
    admin_vat = admin.get("vat_number", "")

    story = []
    # En-tete
    if admin_name:
        story.append(Paragraph(f"<b>{admin_name}</b>", st_normal))
    if admin_address:
        story.append(Paragraph(admin_address, st_normal))
    if admin_vat:
        story.append(Paragraph(f"N&deg; TVA : {admin_vat}", st_normal))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(f"FACTURE - {period_label or 'Periode'}", st_title))
    today = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    story.append(Paragraph(f"Date : {today}", st_normal))
    story.append(Spacer(1, 6 * mm))

    # Client
    story.append(Paragraph("Client", st_h))
    story.append(Paragraph(f"<b>{row['syndic_name']}</b>", st_normal))
    if row.get("syndic_email"):
        story.append(Paragraph(row["syndic_email"], st_normal))
    story.append(Spacer(1, 6 * mm))

    # Detail
    story.append(Paragraph("Detail de la prestation", st_h))
    freq_lbl = {"monthly": "mensuelle", "quarterly": "trimestrielle", "annual": "annuelle"}.get(
        row["frequency"], row["frequency"]
    )
    if row["pricing_mode"] == "negocie":
        desc_line = (
            f"Gestion de {row['acp_count']} copropriete(s), soit {row['lot_count']} lot(s) actif(s). "
            f"Forfait negocie. Facturation {freq_lbl}."
        )
    else:
        tier_desc = " · ".join(
            f"{t.get('min_lots','?')}-{t.get('max_lots','+') if t.get('max_lots') is not None else '+'} : {float(t.get('annual_fee') or t.get('price_per_lot') or 0):.2f} EUR/an"
            for t in cfg.get("tiers", [])
        )
        desc_line = (
            f"Gestion de {row['acp_count']} copropriete(s), soit {row['lot_count']} lot(s) actif(s). "
            f"Bareme par tranche : {tier_desc}. Facturation {freq_lbl}."
        )
    story.append(Paragraph(desc_line, st_normal))
    story.append(Spacer(1, 4 * mm))

    # Tableau montants
    tva = row["vat_rate"]
    data = [
        ["Description", "Montant"],
        [f"Prestation ({freq_lbl})", f"{row['period_amount_ht']:.2f} EUR"],
        [f"TVA {tva}%", f"{row['period_amount_tva']:.2f} EUR"],
        ["Total TTC", f"{row['period_amount_ttc']:.2f} EUR"],
    ]
    tbl = Table(data, colWidths=[110 * mm, 60 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#022D52")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5EDF7")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10 * mm))

    # Paiement
    if admin_iban:
        story.append(Paragraph("Modalites de paiement", st_h))
        story.append(Paragraph(f"IBAN : <b>{admin_iban}</b>", st_normal))
        story.append(Paragraph(
            f"Communication : Facture {period_label or ''} - {row['syndic_name']}",
            st_normal,
        ))

    doc.build(story)
    return buf.getvalue()
