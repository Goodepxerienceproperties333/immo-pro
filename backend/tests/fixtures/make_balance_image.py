"""Generate a realistic Balance de Tiers PNG for iter93cj Vision tests.

Rules from /app/image_testing.md:
- PNG format, real visual content (edges + text + rows/lines), not blank / uniform.
"""
from PIL import Image, ImageDraw, ImageFont
import os


def make():
    W, H = 900, 500
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)

    try:
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except Exception:
        font_bold = ImageFont.load_default()
        font = ImageFont.load_default()

    # Title
    d.text((30, 20), "Balance de Tiers - 31/03/2027", fill=(15, 25, 60), font=font_bold)
    d.line((30, 55, W - 30, 55), fill=(15, 25, 60), width=2)

    # Header row
    headers = ["Compte", "Proprietaire", "Debit", "Credit", "Solde"]
    x_cols = [30, 170, 420, 550, 700]
    y = 75
    for i, h in enumerate(headers):
        d.text((x_cols[i], y), h, fill=(30, 30, 30), font=font_bold)
    d.line((30, y + 28, W - 30, y + 28), fill=(120, 120, 120), width=1)

    rows = [
        ["4100001", "Guerit",       "50,00",  "23,05",  "26,95"],
        ["4100002", "CANTERO",     "600,22", "30,00",  "570,22"],
        ["4100003", "Van Damme",   "215,64",  "0,00",  "215,64"],
        ["4100004", "De Le Hoye",  "500,00", "71,39",  "428,61"],
    ]
    y = 115
    for row in rows:
        for i, cell in enumerate(row):
            d.text((x_cols[i], y), cell, fill=(20, 20, 20), font=font)
        d.line((30, y + 26, W - 30, y + 26), fill=(200, 200, 205), width=1)
        y += 35

    # Total line
    y += 10
    d.line((30, y, W - 30, y), fill=(15, 25, 60), width=2)
    d.text((x_cols[1], y + 8), "TOTAL", fill=(15, 25, 60), font=font_bold)
    d.text((x_cols[4], y + 8), "1 241,42", fill=(15, 25, 60), font=font_bold)

    # Footer
    d.text((30, H - 40), "Copropriete NextGe - Exercice 2026-2027", fill=(80, 80, 80), font=font)

    out = os.path.join(os.path.dirname(__file__), "balance_tiers_iter93cj.png")
    img.save(out, "PNG", optimize=True)
    print(f"Saved: {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    make()
