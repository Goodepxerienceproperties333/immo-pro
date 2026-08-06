"""PDF utilities for Optipro/Sogis exports (Bilan, Budget, Natures, Cles).

Uses pdfplumber for text + table extraction.
"""
import io
from typing import Optional
import pdfplumber


def extract_pdf(raw: bytes) -> dict:
    """Extract text and tables from a PDF file.

    Returns: {
      pages: [{
        page_num: int,
        text: str,
        tables: [ [[cell, ...], ...], ... ]  # raw tables as 2D arrays
      }, ...],
      total_pages: int,
      full_text: str,
    }
    """
    pages_out = []
    full_text_parts = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages_out.append({
                "page_num": i + 1,
                "text": text,
                "tables": tables,
            })
            full_text_parts.append(text)
    return {
        "pages": pages_out,
        "total_pages": len(pages_out),
        "full_text": "\n".join(full_text_parts),
    }


def parse_natures_pdf(raw: bytes) -> dict:
    """Parse 'Liste des categories de depense' PDF from Optipro.

    pdfplumber may extract the whole table as a single row where each cell
    contains newline-separated values. We split each cell by `\\n` and zip
    columns together.

    Expected columns: CODE | LIBELLE | COMPTE | CODE TVA | PART OCCUPANT | PART PROPRIETAIRE
    Returns: { natures: [{code, libelle, account_number, vat_code, part_occupant, part_proprietaire}], count }
    """
    info = extract_pdf(raw)
    natures: list[dict] = []
    seen_codes: set[str] = set()
    for page in info["pages"]:
        for table in page["tables"]:
            if not table or len(table) < 1:
                continue
            header_row = table[0]
            header_norm = [(c or "").lower().strip().replace("\n", " ") for c in header_row]
            col_idx: dict[str, int] = {}
            for i, h in enumerate(header_norm):
                if "code" in h and "tva" not in h:
                    col_idx.setdefault("code", i)
                elif "libelle" in h or "libellé" in h:
                    col_idx.setdefault("libelle", i)
                elif "compte" in h:
                    col_idx.setdefault("account", i)
                elif "tva" in h:
                    col_idx.setdefault("vat", i)
                elif "occupant" in h:
                    col_idx.setdefault("occ", i)
                elif "propri" in h:
                    col_idx.setdefault("own", i)
            if "code" not in col_idx or "libelle" not in col_idx or "account" not in col_idx:
                continue
            # For each data row, each cell can hold multiple lines. Split + zip.
            for row in table[1:]:
                if not row:
                    continue
                # Split each cell by newline
                split_cells: list[list[str]] = []
                max_len = 0
                for c in row:
                    parts = [p.strip() for p in (c or "").split("\n")]
                    parts = [p for p in parts if p]
                    split_cells.append(parts)
                    if len(parts) > max_len:
                        max_len = len(parts)
                if max_len == 0:
                    continue
                # Compact each column to align with codes (codes drive the row count)
                code_cell = split_cells[col_idx["code"]] if col_idx["code"] < len(split_cells) else []
                if not code_cell:
                    continue
                code_count = len(code_cell)
                libelle_cell = _expand_multiline(split_cells[col_idx["libelle"]], code_count) if col_idx["libelle"] < len(split_cells) else []
                account_cell = split_cells[col_idx["account"]] if col_idx["account"] < len(split_cells) else []
                vat_cell = split_cells[col_idx["vat"]] if "vat" in col_idx and col_idx["vat"] < len(split_cells) else []
                occ_cell = split_cells[col_idx["occ"]] if "occ" in col_idx and col_idx["occ"] < len(split_cells) else []
                own_cell = split_cells[col_idx["own"]] if "own" in col_idx and col_idx["own"] < len(split_cells) else []
                for i, code in enumerate(code_cell):
                    code = code.strip()
                    if not code or code in seen_codes:
                        continue
                    seen_codes.add(code)
                    libelle = libelle_cell[i] if i < len(libelle_cell) else ""
                    account = account_cell[i] if i < len(account_cell) else ""
                    vat_raw = vat_cell[i] if i < len(vat_cell) else ""
                    occ_raw = occ_cell[i] if i < len(occ_cell) else ""
                    own_raw = own_cell[i] if i < len(own_cell) else ""
                    # Clean VAT : keep only the code (A1, A2, A4, etc.) without parentheses
                    vat_code = (vat_raw.split("(")[0] or "").strip()
                    if vat_code in ("-", ""):
                        vat_code = ""
                    # Percentages may be wrapped in `(...)` — extract numbers
                    natures.append({
                        "code": code,
                        "libelle": libelle.strip(),
                        "account_number": account.strip(),
                        "vat_code": vat_code,
                        "part_occupant": _extract_pct(occ_raw),
                        "part_proprietaire": _extract_pct(own_raw),
                    })
    return {"natures": natures, "count": len(natures)}


def _expand_multiline(parts: list[str], target_count: int) -> list[str]:
    """Merge wrapped lines back together. If pdfplumber split a long libelle into 2
    rows, we need to merge them so that len(libelles) == len(codes).
    Strategy : naive bottom-up — if we have more libelle lines than codes, merge
    consecutive lines until lengths match.
    """
    if len(parts) <= target_count or target_count == 0:
        return parts + [""] * (target_count - len(parts))
    # Need to merge : compute how many merges needed
    excess = len(parts) - target_count
    merged = list(parts)
    # Heuristic : merge lines that are continuations (don't start with a capital + space, or start lowercase)
    while excess > 0 and len(merged) > target_count:
        # Find best merge candidate (line that doesn't look like a start of a label)
        for i in range(1, len(merged)):
            if merged[i] and (merged[i][0].islower() or merged[i].startswith("installations")
                              or merged[i].startswith("selon") or merged[i].startswith("(")
                              or merged[i].startswith("immediat") or merged[i].startswith("imm")):
                merged[i - 1] = merged[i - 1] + " " + merged[i]
                merged.pop(i)
                excess -= 1
                break
        else:
            # No clear continuation : merge the first two anyway
            merged[0] = merged[0] + " " + merged[1]
            merged.pop(1)
            excess -= 1
    return merged


def _extract_pct(s: str) -> float:
    """Extract a percentage from a string like '(0.00%)' or '100.00%' or '-'."""
    if not s:
        return 0.0
    import re
    m = re.search(r"([\d.,]+)\s*%", s)
    if not m:
        return 0.0
    return _to_float(m.group(1))


# ============================================================
# OWNERS PDF parser (Optipro "Liste des coproprietaires")
# ============================================================
# Expected columns : AUXILIAIRE | NOM | IDENTIFIANT | COORDONNEES (email/phone) | LOTS | QUOTITES | REFERENCE VCS
_CIVILITY_RE = r"^(M\.\s+et/ou\s+Mme\.?|Mme\.?\s+et/ou\s+M\.?|M\.\s+et\s+Mme\.?|Mme\.?\s+et\s+M\.?|M\s+et\s+Mme\.?|Mr\.?\s+et\s+Mme\.?|Mlle\.?|Mme\.?|M\.|Mr\.?|Mr)\s+"


def _normalize_header(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _detect_column_boundaries(header_words: list[dict], label_map: dict[str, str]) -> tuple[dict[str, tuple], float]:
    """Compute column x-boundaries from header word positions.

    label_map: { normalized_header_substring -> column_key }
    Header words on the SAME y-line are grouped. For each column anchor, the
    boundary is the midpoint between consecutive anchor x-centers.

    Returns: (cols dict, header_y_bottom). header_y_bottom is the bottom of the
    actual chosen header row (NOT of all candidates - so stray "header-like"
    words in the page body don't push the cutoff far down).
    """
    # Group header words by row (close in y) - the top-most row is the actual header
    if not header_words:
        return {}, 0.0
    # First : cluster header words into rows by Y proximity (within ~6px)
    sorted_hw = sorted(header_words, key=lambda w: w["top"])
    rows: list[list[dict]] = []
    for w in sorted_hw:
        placed = False
        for row in rows:
            row_top = sum(x["top"] for x in row) / len(row)
            if abs(w["top"] - row_top) <= 6:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    # Merge consecutive rows that are within 12px of each other (multi-line headers
    # like "NATURE DU BIEN" stack on 3 lines but represent ONE column anchor).
    rows.sort(key=lambda r: min(w["top"] for w in r))
    merged_rows: list[list[dict]] = []
    for row in rows:
        if merged_rows:
            last = merged_rows[-1]
            last_max = max(w["bottom"] for w in last)
            row_min = min(w["top"] for w in row)
            if row_min - last_max <= 8:
                merged_rows[-1] = last + row
                continue
        merged_rows.append(row)
    # Pick the merged row with the most labels matched
    best_row = None
    best_count = 0
    for ws in merged_rows:
        matched = 0
        for w in ws:
            nt = _normalize_header(w["text"])
            for needle in label_map:
                if needle in nt:
                    matched += 1
                    break
        if matched > best_count:
            best_count = matched
            best_row = ws
    if not best_row:
        return {}, 0.0
    # For each word in the header row, find its column key
    anchors: list[tuple[float, float, str]] = []  # (x0, x_center, col_key)
    seen_keys: set[str] = set()
    for w in sorted(best_row, key=lambda w: w["x0"]):
        nt = _normalize_header(w["text"])
        for needle, col_key in label_map.items():
            if needle in nt and col_key not in seen_keys:
                anchors.append((w["x0"], (w["x0"] + w["x1"]) / 2, col_key))
                seen_keys.add(col_key)
                break
    if not anchors:
        return {}, 0.0
    # Optipro PDFs : headers are often CENTERED above each column but DATA words
    # are LEFT-aligned starting at (or even BEFORE) the header's x0. Use the
    # midpoint between consecutive header centers - then refine by looking at
    # actual data positions just below the header.
    cols: dict[str, tuple[float, float]] = {}
    n = len(anchors)
    for i in range(n):
        x0_i, cx_i, key = anchors[i]
        if i == 0:
            x_start = 0
        else:
            prev_cx = anchors[i - 1][1]
            x_start = (prev_cx + cx_i) / 2
        if i + 1 < n:
            next_cx = anchors[i + 1][1]
            x_end = (cx_i + next_cx) / 2
        else:
            x_end = 9999
        cols[key] = (x_start, x_end)
    header_y_bottom = max((w["bottom"] for w in best_row), default=0)
    return cols, header_y_bottom


def _refine_columns_from_data(
    cols: dict[str, tuple[float, float]],
    words: list[dict],
    header_y_max: float,
    anchor_predicate,
) -> dict[str, tuple[float, float]]:
    """Refine column x-boundaries using actual data positions.

    Strategy : sample words from the first N data rows. Cluster their x0
    positions, find the K cluster centers nearest to the header column centers,
    then derive new boundaries as midpoints between consecutive cluster centers.
    K = number of columns detected from header.
    """
    anchors = sorted(
        [w for w in words if w["top"] > header_y_max + 1 and anchor_predicate(w)],
        key=lambda w: w["top"],
    )
    if len(anchors) < 1:
        return cols
    sample_anchors = anchors[: min(8, len(anchors))]
    sample_words: list[dict] = []
    for i, a in enumerate(sample_anchors):
        y0 = a["top"] - 1
        if i + 1 < len(sample_anchors):
            y1 = sample_anchors[i + 1]["top"] - 1
        else:
            y1 = a["top"] + 30
        sample_words.extend([w for w in words if y0 <= w["top"] < y1])
    if not sample_words:
        return cols

    # Cluster x0 positions of the sample words. Two x0 values belong to the
    # same cluster if they are within 4px of each other.
    x0s = sorted(w["x0"] for w in sample_words)
    clusters: list[list[float]] = []
    for x in x0s:
        if clusters and x - clusters[-1][-1] <= 4:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    cluster_lefts = [min(c) for c in clusters]

    # Now merge clusters that are TOO CLOSE to be separate columns. Within a
    # single column (e.g. multi-word "Mme CALLENS Bernadette"), word x0 values
    # appear at every 8-15px. A TRUE column gap is typically >= 25px.
    merged_lefts: list[float] = []
    for cl in cluster_lefts:
        if merged_lefts and cl - merged_lefts[-1] < 25:
            continue  # same column as the previous - skip
        merged_lefts.append(cl)
    cluster_lefts = merged_lefts

    # For each header column (in order), find the closest cluster_left whose
    # value is within the column's original boundaries (with wider slack on the
    # LEFT side to catch prefix-style data like "C0XXX - " in owner columns).
    col_keys_sorted = sorted(cols.keys(), key=lambda k: cols[k][0])
    data_starts: dict[str, float] = {}
    used_clusters: set[int] = set()
    for key in col_keys_sorted:
        xs_, xe_ = cols[key]
        best_idx = None
        for idx, cl in enumerate(cluster_lefts):
            if idx in used_clusters:
                continue
            if xs_ - 30 <= cl < xe_ + 15:
                best_idx = idx
                break
        if best_idx is not None:
            data_starts[key] = cluster_lefts[best_idx]
            used_clusters.add(best_idx)
        else:
            data_starts[key] = xs_
    # Build new boundaries
    refined: dict[str, tuple[float, float]] = {}
    for i, key in enumerate(col_keys_sorted):
        start = max(0, data_starts[key] - 3)
        if i + 1 < len(col_keys_sorted):
            next_start = data_starts[col_keys_sorted[i + 1]]
            end = max(start + 5, next_start - 3)
        else:
            end = 9999
        refined[key] = (start, end)
    return refined


def _group_into_rows_by_anchor(
    words: list[dict],
    anchor_predicate,
    header_y_max: float,
) -> list[tuple[float, float, list[dict]]]:
    """Group words into rows using anchor lines.

    An "anchor" is a word that marks the start of a logical row (e.g. owner
    auxiliary code 'C0XXX' or lot code '0001').

    The band for anchor[i] spans from the MIDPOINT between anchor[i-1] and
    anchor[i] (or header_y_max if i=0) to the MIDPOINT between anchor[i] and
    anchor[i+1] (or end-of-page if last). This correctly captures multi-line
    content that wraps either ABOVE or BELOW the anchor line.
    """
    anchors = sorted(
        [w for w in words if w["top"] > header_y_max + 1 and anchor_predicate(w)],
        key=lambda w: w["top"],
    )
    if not anchors:
        return []
    bands: list[tuple[float, float, list[dict]]] = []
    n = len(anchors)
    for i in range(n):
        a_top = anchors[i]["top"]
        prev_top = anchors[i - 1]["top"] if i > 0 else (header_y_max + 1)
        next_top = anchors[i + 1]["top"] if i + 1 < n else (a_top + 9999)
        y0 = (prev_top + a_top) / 2
        y1 = (a_top + next_top) / 2
        in_band = [w for w in words if y0 <= w["top"] < y1]
        bands.append((y0, y1, in_band))
    return bands


def parse_owners_pdf(raw: bytes) -> dict:
    """Parse 'Liste des coproprietaires' PDF from Optipro.

    Strategy (anchor-based):
      1. Detect the header row (AUXILIAIRE / NOM / IDENTIFIANT / COORDONNEES / LOTS / QUOTITES / REFERENCE VCS).
      2. Compute column x-boundaries from header word centers.
      3. Find anchor words (auxiliary codes 'C0XXX') and group all words into
         Y-bands [anchor[i].top, anchor[i+1].top). This correctly captures
         multi-line names that wrap across 2-3 lines.
      4. For each band, assign each word to a column based on its x-center.

    Returns:
      { owners: [{ auxiliary_code, civility, last_name, first_name, name,
                   identifier, email, phone, vcs_code, vcs_digits }],
        count }
    """
    import re
    owners: list[dict] = []
    seen_aux: set[str] = set()
    email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    phone_re = re.compile(r"\+?\d[\d\s.\-/]{6,20}\d")
    vcs_re = re.compile(r"\+{0,3}(\d{3}[/.\-]\d{4}[/.\-]\d{5})\+{0,3}")
    aux_re = re.compile(r"^C\d{4}$")

    label_map = {
        "auxil": "aux",
        "nom": "name",
        "identif": "ident",
        "coordo": "coord",
        "lot": "lots",
        "quotit": "qts",
        "reference": "vcs",
        "vcs": "vcs",
        "communic": "vcs",
    }

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue
            # Detect column boundaries from header
            header_candidates = [w for w in words if any(
                k in _normalize_header(w["text"]) for k in label_map
            )]
            cols, header_y_max = _detect_column_boundaries(header_candidates, label_map)
            if "aux" not in cols or "vcs" not in cols:
                # No usable header on this page (e.g. continuation page) - skip
                continue
            # Refine column boundaries from actual data positions (Optipro headers
            # are centered, data is left-aligned - they don't line up exactly)
            cols = _refine_columns_from_data(
                cols, words, header_y_max,
                anchor_predicate=lambda w: bool(aux_re.match(w["text"])),
            )

            bands = _group_into_rows_by_anchor(
                words,
                anchor_predicate=lambda w: bool(aux_re.match(w["text"])),
                header_y_max=header_y_max,
            )
            for _y0, _y1, band_words in bands:
                cells: dict[str, list[dict]] = {k: [] for k in cols}
                for w in band_words:
                    # Optipro data is LEFT-aligned: use x0 to assign columns.
                    px = w["x0"]
                    for col_name, (xs_, xe_) in cols.items():
                        if xs_ <= px < xe_:
                            cells[col_name].append(w)
                            break

                def cell_lines(name: str) -> list[str]:
                    """Return cell content grouped by row (preserve line order)."""
                    ws = cells.get(name, [])
                    if not ws:
                        return []
                    by_y: dict[int, list[dict]] = {}
                    for w in ws:
                        k = int(w["top"] / 4)
                        by_y.setdefault(k, []).append(w)
                    lines = []
                    for k in sorted(by_y.keys()):
                        row_ws = sorted(by_y[k], key=lambda w: w["x0"])
                        lines.append(" ".join(w["text"] for w in row_ws))
                    return lines

                def cell_text(name: str) -> str:
                    return " ".join(cell_lines(name)).strip()

                aux = cell_text("aux")
                if not aux_re.match(aux):
                    continue
                if aux in seen_aux:
                    continue
                seen_aux.add(aux)

                # Name : may be on multiple lines (civility on line 1, last+first on line 2)
                name_lines = cell_lines("name")
                ident = cell_text("ident")
                coord_raw = cell_text("coord")
                vcs_raw = cell_text("vcs")
                row_text_all = " ".join(w["text"] for w in sorted(band_words, key=lambda w: w["x0"]))
                if not vcs_re.search(vcs_raw):
                    m = vcs_re.search(row_text_all)
                    if m:
                        vcs_raw = m.group(0)

                # ---- Decompose name lines into civility / last / first ----
                # Heuristic : join all name lines, then split civility prefix
                name_joined = " ".join(name_lines).strip()
                civility = ""
                # Try regex on the longest prefix
                mc = re.match(_CIVILITY_RE, name_joined)
                name_clean = name_joined
                if mc:
                    civility = mc.group(1).strip()
                    name_clean = name_joined[mc.end():].strip()

                # Split last_name (uppercase) vs first_name (mixed case)
                # Special case for hyphenated last names like "MOUCHET-GERMAIN"
                tokens = name_clean.split()
                last_tokens: list[str] = []
                first_tokens: list[str] = []
                for tok in tokens:
                    # uppercase or contains uppercase + hyphen pattern like "MOUCHET-GERMAIN"
                    is_upper = tok.isupper() or (
                        "-" in tok and all(part.isupper() or not part for part in tok.split("-"))
                    )
                    # also treat name fragments ending with "-" as last name (wraps)
                    if is_upper or tok.endswith("-"):
                        if first_tokens:
                            # already started first name -> stop accumulating last_name
                            first_tokens.append(tok)
                        else:
                            last_tokens.append(tok)
                    else:
                        first_tokens.append(tok)
                last_name = " ".join(last_tokens).rstrip("-").strip()
                first_name = " ".join(first_tokens).strip()
                # Fallback : if last_name is empty (e.g. "Calbert Nelly" all lowercase)
                if not last_name and tokens:
                    last_name = tokens[0]
                    first_name = " ".join(tokens[1:])

                email_m = email_re.search(coord_raw)
                email = email_m.group(0) if email_m else ""
                coord_for_phone = coord_raw.replace(email, " ") if email else coord_raw
                phone_m = phone_re.search(coord_for_phone)
                phone = phone_m.group(0).strip() if phone_m else ""

                vcs_m = vcs_re.search(vcs_raw)
                vcs_code = vcs_m.group(0).strip() if vcs_m else (vcs_raw if vcs_raw not in ("-", "") else "")
                vcs_digits = re.sub(r"\D", "", vcs_code)

                owners.append({
                    "auxiliary_code": aux,
                    "civility": civility,
                    "last_name": last_name,
                    "first_name": first_name,
                    "name": (civility + " " + name_clean).strip(),
                    "identifier": ident if ident not in ("-", "") else "",
                    "email": email,
                    "phone": phone,
                    "vcs_code": vcs_code,
                    "vcs_digits": vcs_digits,
                })
    return {"owners": owners, "count": len(owners)}


# ============================================================
# LOTS PDF parser (Optipro "Liste des lots")
# ============================================================
def parse_lots_pdf(raw: bytes) -> dict:
    """Parse 'Liste des lots' PDF from Optipro.

    Expected columns : CODE | REFERENCE | NATURE DU BIEN | BATIMENT | QUOTITES |
                       PROPRIETAIRE ACTUEL | ADRESSE

    Anchor predicate : 4-digit code in the leftmost column (x0 < 80).

    Returns:
      { lots: [{ code, reference, nature, batiment, quotites, quotities_value,
                 owner_auxiliary_code, owner_name, address }],
        count }
    """
    import re
    lots: list[dict] = []
    seen_codes: set[str] = set()
    aux_re = re.compile(r"\bC\d{4}\b")
    code_re = re.compile(r"^\d{4}$")

    label_map = {
        "code": "code",
        "reference": "reference",
        "nature": "nature",
        "bien": "nature",
        "batiment": "batiment",
        "quotit": "qts",
        "proprietaire": "owner",
        "actuel": "owner",
        "adresse": "address",
    }

    def _to_float(s: str) -> float:
        s = (s or "").strip().replace(" ", "").replace(",", ".")
        if not s or s == "-":
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue
            # Header detection - take only the header line(s), excluding stray header
            # words from the page title block (limit to y < 130)
            header_candidates = [w for w in words if w["top"] < 130 and any(
                k in _normalize_header(w["text"]) for k in label_map
            )]
            cols, header_y_max = _detect_column_boundaries(header_candidates, label_map)
            if "code" not in cols or "owner" not in cols:
                continue

            def _is_anchor(w):
                return bool(code_re.match(w["text"])) and w["x0"] < 80

            cols = _refine_columns_from_data(cols, words, header_y_max, _is_anchor)

            bands = _group_into_rows_by_anchor(words, _is_anchor, header_y_max)
            for _y0, _y1, band_words in bands:
                cells: dict[str, list[dict]] = {k: [] for k in cols}
                for w in band_words:
                    px = w["x0"]
                    for col_name, (xs_, xe_) in cols.items():
                        if xs_ <= px < xe_:
                            cells[col_name].append(w)
                            break

                def ctxt(name: str) -> str:
                    ws = cells.get(name, [])
                    if not ws:
                        return ""
                    by_y: dict[int, list[dict]] = {}
                    for w in ws:
                        k = int(w["top"] / 4)
                        by_y.setdefault(k, []).append(w)
                    parts = []
                    for k in sorted(by_y.keys()):
                        row_ws = sorted(by_y[k], key=lambda w: w["x0"])
                        parts.append(" ".join(w["text"] for w in row_ws))
                    return " ".join(parts).strip()

                code = ctxt("code")
                if not code_re.match(code):
                    continue
                if code in seen_codes:
                    continue
                seen_codes.add(code)

                reference = ctxt("reference")
                nature = ctxt("nature")
                batiment = ctxt("batiment")
                quotites_raw = ctxt("qts")
                owner_raw = ctxt("owner")
                address = ctxt("address")

                # Parse owner field : "C0946 - M. LEGROS Jean"
                owner_aux = ""
                owner_name = owner_raw
                aux_m = aux_re.search(owner_raw)
                if aux_m:
                    owner_aux = aux_m.group(0)
                    # Strip "C0XXX - " prefix
                    owner_name = re.sub(r"^C\d{4}\s*-\s*", "", owner_raw).strip()
                # Clean stray dashes
                if owner_name in ("-", ""):
                    owner_name = ""
                if address in ("-", ""):
                    address = ""
                if batiment in ("-", ""):
                    batiment = ""
                quotites_value = _to_float(quotites_raw)

                lots.append({
                    "code": code,
                    "reference": reference if reference and reference != "-" else code,
                    "nature": nature,
                    "batiment": batiment,
                    "quotites": quotites_raw if quotites_raw != "-" else "",
                    "quotities_value": quotites_value,
                    "owner_auxiliary_code": owner_aux,
                    "owner_name": owner_name,
                    "address": address,
                })
    return {"lots": lots, "count": len(lots)}


# ============================================================
# SUPPLIERS PDF parser (Optipro "Liste des fournisseurs")
# ============================================================
def parse_suppliers_pdf(raw: bytes) -> dict:
    """Parse 'Liste des fournisseurs' PDF from Optipro.

    Expected columns : AUXILIAIRE | NOM | PAR DEFAUT | COORDONNEES (email/phone) | ADRESSE

    Anchor predicate : auxiliary code matching `^F\\d{4}$` in the leftmost
    column (x0 < 80).

    Returns:
      { suppliers: [{ auxiliary_code, name, is_default, email, phone,
                       address, postal_code, city, country }],
        count }
    """
    import re
    suppliers: list[dict] = []
    seen_aux: set[str] = set()
    email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    phone_re = re.compile(r"\+?\d[\d\s.\-/]{6,20}\d")
    aux_re = re.compile(r"^F\d{4}$")
    # Belgian postal code pattern : 4 digits + city name (multi-word) + ", Country"
    address_pc_re = re.compile(r"(.+?)\s+(\d{4})\s+(.+?)(?:\s*,\s*(\w+))?$")

    label_map = {
        "auxil": "aux",
        "nom": "name",
        "par": "default",       # PAR DEFAUT header (split on 2 lines : "PAR" then "DEFAUT")
        "defaut": "default",
        "coordo": "coord",
        "adresse": "address",
    }

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue
            # Header detection - limit to page top
            header_candidates = [w for w in words if w["top"] < 130 and any(
                k in _normalize_header(w["text"]) for k in label_map
            )]
            cols, header_y_max = _detect_column_boundaries(header_candidates, label_map)
            if "aux" not in cols or "name" not in cols:
                continue

            def _is_anchor(w):
                return bool(aux_re.match(w["text"])) and w["x0"] < 80

            cols = _refine_columns_from_data(cols, words, header_y_max, _is_anchor)
            bands = _group_into_rows_by_anchor(words, _is_anchor, header_y_max)

            for _y0, _y1, band_words in bands:
                cells: dict[str, list[dict]] = {k: [] for k in cols}
                for w in band_words:
                    px = w["x0"]
                    for col_name, (xs_, xe_) in cols.items():
                        if xs_ <= px < xe_:
                            cells[col_name].append(w)
                            break

                def ctxt(name: str) -> str:
                    ws = cells.get(name, [])
                    if not ws:
                        return ""
                    by_y: dict[int, list[dict]] = {}
                    for w in ws:
                        k = int(w["top"] / 4)
                        by_y.setdefault(k, []).append(w)
                    parts = []
                    for k in sorted(by_y.keys()):
                        row_ws = sorted(by_y[k], key=lambda w: w["x0"])
                        parts.append(" ".join(w["text"] for w in row_ws))
                    return " ".join(parts).strip()

                aux = ctxt("aux")
                if not aux_re.match(aux):
                    continue
                if aux in seen_aux:
                    continue
                seen_aux.add(aux)

                name = ctxt("name")
                default_raw = ctxt("default").strip().lower()
                coord_raw = ctxt("coord")
                address_raw = ctxt("address")

                # Parse coord field for email + phone
                email_m = email_re.search(coord_raw)
                email = email_m.group(0) if email_m else ""
                coord_for_phone = coord_raw.replace(email, " ") if email else coord_raw
                phone_m = phone_re.search(coord_for_phone)
                phone = phone_m.group(0).strip() if phone_m else ""

                # Parse address : try to extract postal_code + city
                postal_code = ""
                city = ""
                country = ""
                address_clean = address_raw.strip()
                if address_clean and address_clean != "-":
                    am = address_pc_re.match(address_clean)
                    if am:
                        street, postal_code, city_part, country_part = am.groups()
                        address_clean = street.strip().rstrip(",").strip()
                        city = (city_part or "").strip().rstrip(",").strip()
                        country = (country_part or "").strip()
                else:
                    address_clean = ""

                is_default = default_raw in ("oui", "yes", "true", "1")

                suppliers.append({
                    "auxiliary_code": aux,
                    "name": name,
                    "is_default": is_default,
                    "email": email,
                    "phone": phone,
                    "address": address_clean,
                    "postal_code": postal_code,
                    "city": city,
                    "country": country or "Belgique",
                })
    return {"suppliers": suppliers, "count": len(suppliers)}


# ============================================================
# BUDGET PDF parser
# ============================================================
def parse_budget_pdf(raw: bytes) -> dict:
    """Parse 'Budget' PDF from Optipro.

    Structure : sections grouped by distribution key code (4-digit code starting
    with 0, e.g. "0001", "0006", "0008"). Each section has PCMN account lines
    (3-5 digits NOT starting with 0, e.g. "61037", "650", "6160").

    The PDF has 3 amount columns at the right side:
      - Realise N-1 (Realise 2025)
      - Budget N (Budget 2026)
      - En cours

    Anchor strategy :
      - Section anchor : 4-digit code matching `^0\\d{3}$` at x0 < 50.
      - Detail anchor : 3-5 digit code matching `^\\d{3,5}$` at x0 >= 50.
      - Libelles can wrap to multiple lines (band capture).

    Returns: { sections: [{ key_code, key_label, realise_n1, budget_n, en_cours,
                            lines: [{account, libelle, realise_n1, budget_n, en_cours}] }],
               total_global, count }
    """
    import re
    sections: list[dict] = []
    section_anchor_re = re.compile(r"^0\d{3}$")
    detail_anchor_re = re.compile(r"^\d{3,5}$")
    amount_word_re = re.compile(r"^-?[\d.,]+$")

    def _join_amount(words: list[dict]) -> float:
        """Join numeric word fragments into a single float.
        e.g. '25', '507,50' -> 25507.50 ; '-585,89' stays '-585.89'."""
        if not words:
            return 0.0
        # Sort by x0 to get left-to-right order
        ws = sorted(words, key=lambda w: w["x0"])
        joined = "".join(w["text"] for w in ws).replace(" ", "").replace(",", ".")
        joined = joined.replace("\u00a0", "")  # nbsp
        try:
            return float(joined)
        except ValueError:
            return 0.0

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        current_section: dict = None  # Persist across pages : details on page N+1
                                       # without a preceding section anchor are
                                       # continuation of the LAST section from page N.
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue
            # ---- Determine the PER-PAGE header band Y range ----
            # The header has 3 lines : "Réalisé Budget" / "Désignation" / "2025 2026".
            # We find the LOWEST Y of the year row, which marks the start of data.
            header_year_y = None
            for w in words:
                if re.match(r"^20\d{2}$", w["text"]) and w["x0"] > 400:
                    if header_year_y is None or w["top"] < header_year_y:
                        # Take the FIRST year row (smallest Y) which is the header
                        header_year_y = w["top"]
            # If no year row found (rare), fallback to the "Désignation" position
            header_min_y = (header_year_y + 8) if header_year_y is not None else 195

            # Determine amount column centers from the YEAR words ("2025", "2026")
            # AND from the actual numeric data words (the right-aligned amounts
            # may be 50-100px to the right of the header due to Optipro layout).
            header_centers: list[float] = []
            for w in words:
                # The year header is at top = header_year_y on each page
                if header_year_y is not None and abs(w["top"] - header_year_y) < 3 and re.match(r"^20\d{2}$", w["text"]):
                    header_centers.append((w["x0"] + w["x1"]) / 2)
            # The "En cours" column is detected by the "cours" word
            en_cours_w = next((w for w in words if header_year_y is not None and w["top"] < header_year_y and w["text"].lower() == "cours"), None)

            # ---- AMOUNT CLUSTERING : robust column detection from data ----
            # Collect right-edges of every numeric word below the header. Cluster
            # them into 2-3 groups (1D k-means light) to find the real column
            # centers, which may differ from the year-header centers.
            data_amt_xc: list[float] = []
            num_re = re.compile(r"^-?[\d.,]+$")
            data_words_for_xc: list[dict] = []
            for w in words:
                if w["top"] <= header_min_y:
                    continue
                if w["x0"] < 400:  # left of the amount zone
                    continue
                if num_re.match(w["text"]):
                    # Use x1 (right edge) since amounts are right-aligned
                    data_amt_xc.append(w["x1"])
                    data_words_for_xc.append(w)
            # Greedy 1D clustering (gap threshold 25)
            data_amt_xc.sort()
            data_clusters: list[list[float]] = []
            for x in data_amt_xc:
                if data_clusters and abs(x - data_clusters[-1][-1]) < 25:
                    data_clusters[-1].append(x)
                else:
                    data_clusters.append([x])
            # Fix Optipro "thousands fragment" : in budgets with amounts > 999, the
            # PDF splits "18 800,00" into 2 words ("18" + "800,00"), creating an
            # artefact cluster ~25-30px LEFT of the real amount cluster. The
            # fragment cluster contains ONLY short integer values (no comma).
            # We merge such fragment clusters into the next (real) cluster.
            def _is_fragment_cluster(cluster: list[float]) -> bool:
                """A cluster is a thousand-fragment if all its words are 1-3 digit
                integers (no comma) at the same right-edge."""
                # Find the words at these right-edges
                xs_set = {round(x, 1) for x in cluster}
                texts = [w["text"] for w in data_words_for_xc if round(w["x1"], 1) in xs_set]
                if not texts:
                    return False
                return all(re.match(r"^-?\d{1,3}$", t) for t in texts)

            i = 0
            merged_clusters: list[list[float]] = []
            while i < len(data_clusters):
                cur = data_clusters[i]
                # If next cluster exists and is within 35px AND current is fragment-like
                if i + 1 < len(data_clusters):
                    gap = min(data_clusters[i + 1]) - max(cur)
                    if gap < 35 and _is_fragment_cluster(cur):
                        merged = cur + data_clusters[i + 1]
                        merged_clusters.append(merged)
                        i += 2
                        continue
                merged_clusters.append(cur)
                i += 1
            data_clusters = merged_clusters
            # Keep only clusters with >= 3 points (real columns), then take their
            # mean as column right-edge. Center = right_edge - 12 (avg width).
            data_col_right_edges = [sum(c) / len(c) for c in data_clusters if len(c) >= 3]
            data_col_centers = [e - 12 for e in data_col_right_edges]
            data_col_centers.sort()

            # Prefer data-driven columns if they look sensible (2 or 3 columns)
            if 2 <= len(data_col_centers) <= 3:
                amt_col_centers = data_col_centers
            elif header_centers:
                # Header may have year-range tokens like "2024 - 2025" creating
                # 2 year-tokens per column. Cluster header centers too (gap < 35).
                header_centers_sorted = sorted(header_centers)
                hc_clusters: list[list[float]] = []
                for x in header_centers_sorted:
                    if hc_clusters and (x - hc_clusters[-1][-1]) < 35:
                        hc_clusters[-1].append(x)
                    else:
                        hc_clusters.append([x])
                amt_col_centers = [sum(c) / len(c) for c in hc_clusters]
                if en_cours_w:
                    amt_col_centers.append((en_cours_w["x0"] + en_cours_w["x1"]) / 2)
                amt_col_centers.sort()
            else:
                # Last-resort default Optipro positions
                amt_col_centers = [607.0, 782.0]

            # Build amount column boundaries using clean midpoints (no overlap)
            amt_bounds: list[tuple[float, float, str]] = []  # (xs, xe, key)
            # Map column count -> key names (1 col = budget only, 2 cols = N-1 + N,
            # 3 cols = N-1 + N + en_cours). Truncate to length of amt_col_centers
            # to avoid IndexError on PDFs with unusual column layouts.
            if len(amt_col_centers) == 1:
                keys = ["budget_n"]
            elif len(amt_col_centers) == 2:
                keys = ["realise_n1", "budget_n"]
            else:
                keys = ["realise_n1", "budget_n", "en_cours"]
            # Defensive : if amt_col_centers has more entries than keys, truncate.
            amt_col_centers = amt_col_centers[: len(keys)]
            for i, cx in enumerate(amt_col_centers):
                if i == 0:
                    xs = cx - 60
                else:
                    xs = (amt_col_centers[i - 1] + cx) / 2
                if i + 1 < len(amt_col_centers):
                    xe = (cx + amt_col_centers[i + 1]) / 2
                else:
                    xe = cx + 60
                amt_bounds.append((xs, xe, keys[i]))

            # Detect "Totaux" / "Total" row to stop band scanning (page footer summary)
            totaux_y = None
            for w in words:
                t = w["text"].lower().strip(":")
                if t in ("totaux", "total"):
                    totaux_y = w["top"]
                    break

            # Anchors (sections AND details) sorted by Y
            def _is_anchor(w):
                t = w["text"]
                if section_anchor_re.match(t) and w["x0"] < 50:
                    return ("section", t)
                if detail_anchor_re.match(t) and 50 <= w["x0"] < 90 and not t.startswith("0"):
                    return ("detail", t)
                return None

            anchors = []
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                kind = _is_anchor(w)
                if not kind or w["top"] <= header_min_y:
                    continue
                # Skip anchors below the "Totaux" summary row
                if totaux_y is not None and w["top"] >= totaux_y - 1:
                    continue
                anchors.append((w["top"], w["x0"], kind[0], kind[1], w))

            if not anchors:
                continue

            n = len(anchors)
            for i in range(n):
                a_top, a_x0, a_kind, a_code, anchor_w = anchors[i]
                # Band ends at next anchor's top OR at the "Totaux" row (whichever comes first)
                next_anchor_top = anchors[i + 1][0] if i + 1 < n else a_top + 500
                band_end = next_anchor_top
                if totaux_y is not None and totaux_y < band_end:
                    band_end = totaux_y

                # Collect words in the Y band [a_top - 1, band_end - 1)
                band_words = [w for w in words if a_top - 1 <= w["top"] < band_end - 1]

                # Libelle words : x BETWEEN anchor.x1 (right edge of code) + 2px AND
                # the leftmost amount column start. For SECTIONS, allow wrap lines
                # at low x (x < 50) too - they continue the section libelle.
                libelle_xmax = amt_bounds[0][0] - 10  # just before first amount col
                libelle_words = []
                for w in band_words:
                    if w is anchor_w:
                        continue
                    # Skip amount-zone words
                    if w["x0"] >= amt_bounds[0][0] - 10:
                        continue
                    # Skip the dash " - " word that immediately follows the code
                    if w["text"] in ("-", "–") and abs(w["top"] - a_top) < 4 and 60 < w["x0"] < 100:
                        continue
                    if a_kind == "section":
                        # Section libelles may wrap at low x (e.g. "goya)" at x~42)
                        if w["x0"] < libelle_xmax:
                            libelle_words.append(w)
                    else:  # detail
                        # Detail libelles : x in [70, libelle_xmax]
                        if 65 <= w["x0"] < libelle_xmax:
                            libelle_words.append(w)
                libelle_words.sort(key=lambda w: (w["top"], w["x0"]))
                libelle_parts = [w["text"] for w in libelle_words]
                libelle = " ".join(libelle_parts).strip()
                libelle = re.sub(r"^[-–]\s*", "", libelle).strip()

                # Amount columns : group amount words by row, take the row with the
                # most numeric words in amount zone (= the section/detail summary row).
                row_buckets: dict[int, list[dict]] = {}
                for w in band_words:
                    if not amount_word_re.match(w["text"]):
                        continue
                    if w["x0"] < amt_bounds[0][0] - 5:
                        continue
                    by_y = int(w["top"] / 4)
                    row_buckets.setdefault(by_y, []).append(w)
                amounts: dict[str, float] = {"realise_n1": 0.0, "budget_n": 0.0, "en_cours": 0.0}
                if row_buckets:
                    # Prefer the row CLOSEST to the anchor (earliest Y) with at least
                    # 2 amount words. If only one row has multiple amounts, use it.
                    anchor_bucket = int(a_top / 4)
                    best_y = None
                    candidates = sorted(row_buckets.keys(), key=lambda k: abs(k - anchor_bucket))
                    for cand in candidates:
                        if len(row_buckets[cand]) >= 2:
                            best_y = cand
                            break
                    if best_y is None:
                        best_y = candidates[0]
                    row_amt_words = sorted(row_buckets[best_y], key=lambda w: w["x0"])
                    # ---- Group contiguous amount words (gap < 15px) into single
                    # "amount groups". Optipro splits "30 051,00" into two words
                    # at the thin-space, which breaks naive column matching. We
                    # rebuild full amounts before assigning them to columns.
                    amount_groups: list[list[dict]] = []
                    for w in row_amt_words:
                        if amount_groups and (w["x0"] - amount_groups[-1][-1]["x1"]) < 15:
                            amount_groups[-1].append(w)
                        else:
                            amount_groups.append([w])
                    # Match each group to a column by its RIGHT edge (right-aligned)
                    for grp in amount_groups:
                        right_edge = grp[-1]["x1"]
                        # Find the column whose [xs, xe] contains right_edge
                        best_key = None
                        for xs_, xe_, key in amt_bounds:
                            # Loose match : right_edge must be within (xs - 10, xe + 20)
                            if (xs_ - 10) <= right_edge <= (xe_ + 20):
                                best_key = key
                                break
                        if best_key is None:
                            # Fallback : nearest column by absolute distance to right edge
                            best_key = min(amt_bounds, key=lambda b: min(abs(right_edge - b[0]), abs(right_edge - b[1])))[2]
                        amounts[best_key] = _join_amount(grp)

                if a_kind == "section":
                    # Detection des cles speciales : libelle commence par "Cle Speciale[s]"
                    # ou contient "speciale[s]" (insensible aux accents et a la casse).
                    label_lower = (libelle or "").lower()
                    label_norm = re.sub(r"[éèê]", "e", label_lower)
                    is_special = bool(
                        re.search(r"\bcl[eé]s?\s+sp[eé]ciale", label_lower) or
                        re.search(r"\bcle\s+speciale", label_norm) or
                        re.search(r"\bsp[eé]ciale[s]?\b", label_lower)
                    )
                    current_section = {
                        "key_code": a_code,
                        "key_label": libelle,
                        "is_special": is_special,
                        "realise_n1": amounts["realise_n1"],
                        "budget_n": amounts["budget_n"],
                        "en_cours": amounts["en_cours"],
                        "lines": [],
                        "subtotal": 0.0,
                    }
                    sections.append(current_section)
                else:  # detail
                    if current_section is None:
                        # detail before any section : create a "default" section
                        current_section = {
                            "key_code": "????",
                            "key_label": "(Sans section)",
                            "is_special": False,
                            "realise_n1": 0.0,
                            "budget_n": 0.0,
                            "en_cours": 0.0,
                            "lines": [],
                            "subtotal": 0.0,
                        }
                        sections.append(current_section)
                    current_section["lines"].append({
                        "account": a_code,
                        "libelle": libelle,
                        "realise_n1": amounts["realise_n1"],
                        "budget_n": amounts["budget_n"],
                        "en_cours": amounts["en_cours"],
                        # legacy field for back-compat with existing commit endpoint
                        "amount": amounts["budget_n"],
                    })
                    current_section["subtotal"] += amounts["budget_n"]
            # endfor anchors of page

    total_global = sum(s.get("budget_n", 0.0) for s in sections)
    return {
        "sections": sections,
        "total_global": round(total_global, 2),
        "count": sum(len(s["lines"]) for s in sections),
    }


# ============================================================
# BALANCE SHEET PDF parser (Optipro "Bilan comptable") - Phase I OD ouverture
# ============================================================
def parse_balance_pdf(raw: bytes) -> dict:
    """Parse Optipro 'Bilan comptable' PDF (balance sheet at year-end).

    Structure : 2 columns side-by-side
      - LEFT  : Actif (Assets)  : x in [40, 420]
      - RIGHT : Passif (Liabilities) : x in [420, 800]

    Each side has:
      - Main account rows (3-4 digit code, e.g. "410", "550472")
      - Detail sub-account rows (7-digit code, e.g. "4100960", "4400025")

    Total actif = Total passif (balanced by construction).

    Returns:
      { actif: [{ account, label, amount, is_subaccount }],
        passif: [{ account, label, amount, is_subaccount }],
        total_actif, total_passif, balanced,
        period_end_date }
    """
    import re
    info = {"actif": [], "passif": [], "total_actif": 0.0, "total_passif": 0.0,
            "balanced": False, "period_end_date": ""}
    account_re = re.compile(r"^\d{2,10}$")
    amount_word_re = re.compile(r"^-?[\d.,]+$")

    def _join_amount(words: list[dict]) -> float:
        if not words:
            return 0.0
        ws = sorted(words, key=lambda w: w["x0"])
        joined = "".join(w["text"] for w in ws).replace(" ", "").replace(",", ".").replace("\u00a0", "")
        try:
            return float(joined)
        except ValueError:
            return 0.0

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        # iter93s : cache la geometrie de la 1ere page (position des colonnes
        # Actif / Passif) pour la reutiliser sur les pages suivantes qui
        # n'ont pas les en-tetes "Actif" / "Passif" (bilan multi-pages).
        cached_split_x = None
        cached_actif_x = None
        cached_passif_x = None
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue

            # Detect period end date from header
            for w in words:
                if w["top"] < 60:
                    m = re.match(r"^(\d{2}/\d{2}/\d{4})$", w["text"])
                    if m:
                        info["period_end_date"] = m.group(1)

            # Detect "Actif" / "Passif" headers to determine the split x
            actif_x = passif_x = None
            for w in words:
                if w["top"] < 180 and w["text"].lower() == "actif":
                    actif_x = (w["x0"] + w["x1"]) / 2
                elif w["top"] < 180 and w["text"].lower() == "passif":
                    passif_x = (w["x0"] + w["x1"]) / 2
            if actif_x is None or passif_x is None:
                # iter93s : reprend la geometrie cachee si disponible
                if cached_actif_x is not None and cached_passif_x is not None:
                    actif_x = cached_actif_x
                    passif_x = cached_passif_x
                else:
                    continue
            else:
                cached_actif_x = actif_x
                cached_passif_x = passif_x
            split_x = (actif_x + passif_x) / 2
            cached_split_x = split_x

            # Detect Total actif / Total passif row to stop scanning
            total_y = None
            for w in words:
                if w["text"].lower() == "total":
                    total_y = w["top"]
                    break

            # Find data anchors : account codes (3-7 digits)
            def _is_anchor(w):
                return bool(account_re.match(w["text"]))

            anchors = []
            # iter93s : sur les pages de continuation (page > 1) sans en-tete
            # "Actif/Passif", les donnees commencent souvent haut sur la page
            # (top ~= 30 ou 40). On abaisse le seuil de rejet du header.
            top_reject = 180 if page_idx == 0 else 30
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                if not _is_anchor(w):
                    continue
                if w["top"] <= top_reject:
                    continue
                if total_y is not None and w["top"] >= total_y - 1:
                    continue
                # Side detection : center x of the word relative to split
                cx = (w["x0"] + w["x1"]) / 2
                side = "actif" if cx < split_x else "passif"
                # iter94b : REJECT anchors that fall inside the AMOUNT column
                # of their side. Belgian format uses space as thousand separator,
                # so an amount like "131 472,36" is extracted as TWO words:
                # "131" and "472,36". The leading "131" matches `\d{2,10}` and
                # would be mis-identified as a sub-account (creating a phantom
                # line with amount = the trailing fragment). This corrupts the
                # opening balance sum by exactly the trailing fragment value.
                # Amount columns start at x0 >= 350 (actif) or x0 >= 730 (passif).
                if side == "actif" and w["x0"] >= 350:
                    continue
                if side == "passif" and w["x0"] >= 730:
                    continue
                # Sub-account if x0 > 60 (Actif) or x0 > 435 (Passif)
                if side == "actif":
                    is_sub = w["x0"] > 60
                else:
                    is_sub = w["x0"] > 435
                # 2-digit accounts (e.g. "14 - Resultat exercice", "10 - Capital")
                # are ONLY ever main accounts. Reject if found in a sub-account
                # x-position to avoid false positives from amount fragments
                # (e.g. "10" inside "10 262,39" amount).
                if len(w["text"]) == 2 and is_sub:
                    continue
                anchors.append((w["top"], w["x0"], side, w["text"], w, is_sub))

            # Group anchors by (side, top) - one row per side at each Y position
            # Then for each anchor, find the libellé (text words AFTER the code)
            # and the amount (numeric words RIGHT side of the side zone).
            for top, ax0, side, code, anchor_w, is_sub in anchors:
                # Y band : just this row (top +/- 4)
                row_ws = [w for w in words if abs(w["top"] - top) < 4]
                # Filter by side
                if side == "actif":
                    side_ws = [w for w in row_ws if (w["x0"] + w["x1"]) / 2 < split_x]
                    amount_xmin = 350  # amounts right-aligned in Actif column
                else:
                    side_ws = [w for w in row_ws if (w["x0"] + w["x1"]) / 2 >= split_x]
                    amount_xmin = 730  # amounts right-aligned in Passif column

                # Sort by x0
                side_ws.sort(key=lambda w: w["x0"])
                # libellé : words between (anchor.x1 + 4) and amount_xmin
                # ALSO skip a dash separator if present right after the code
                libelle_words = []
                amount_words = []
                for w in side_ws:
                    if w is anchor_w:
                        continue
                    if amount_word_re.match(w["text"]) and w["x0"] >= amount_xmin:
                        amount_words.append(w)
                    elif w["text"] in ("-", "–") and abs(w["x0"] - (anchor_w["x1"] + 2)) < 5:
                        continue
                    else:
                        libelle_words.append(w)
                libelle = " ".join(w["text"] for w in libelle_words).strip()
                libelle = re.sub(r"^[-–]\s*", "", libelle)
                amount = _join_amount(amount_words)

                entry = {
                    "account": code,
                    "label": libelle,
                    "amount": amount,
                    "is_subaccount": is_sub,
                }
                info[side].append(entry)

            # Capture Total actif / Total passif
            # iter93s : uniquement si detecte sur cette page ET valeur non nulle
            # -> evite d'ecraser les totaux de la page precedente avec 0 sur les
            # pages intermediaires d'un bilan multi-pages.
            if total_y is not None:
                total_ws = [w for w in words if abs(w["top"] - total_y) < 4 and amount_word_re.match(w["text"])]
                actif_amt_ws = [w for w in total_ws if (w["x0"] + w["x1"]) / 2 < split_x and w["x0"] >= 350]
                passif_amt_ws = [w for w in total_ws if (w["x0"] + w["x1"]) / 2 >= split_x and w["x0"] >= 730]
                _ta = _join_amount(actif_amt_ws)
                _tp = _join_amount(passif_amt_ws)
                if _ta > 0:
                    info["total_actif"] = _ta
                if _tp > 0:
                    info["total_passif"] = _tp

    info["balanced"] = abs(info["total_actif"] - info["total_passif"]) < 0.01
    return info


# ============================================================
# DISTRIBUTION KEYS PDF parser
# ============================================================
def parse_distribution_keys_pdf(raw: bytes) -> dict:
    """Parse 'Cle de repartition' PDF from Optipro.

    Each key has : code + libelle + lots with quotities + total.
    Returns: { keys: [{code, name, type, lines: [{lot_label, lot_code, quotity}], total_quotities}] }
    """
    info = extract_pdf(raw)
    keys: list[dict] = []
    seen_codes: set[str] = set()

    import re
    for page in info["pages"]:
        # Try to extract structured tables first
        for table in page["tables"]:
            if not table or len(table) < 1:
                continue
            # Heuristic : if the table has a "TOTAL QUOTITES" column, parse it
            header_norm = [(c or "").lower().strip().replace("\n", " ") for c in table[0]]
            has_total_qt = any("total" in h and ("quotit" in h or "qt" in h) for h in header_norm)
            has_libelle = any("libelle" in h or "libellé" in h for h in header_norm)
            if not (has_total_qt and has_libelle):
                continue
            # Locate columns
            col_idx: dict[str, int] = {}
            for i, h in enumerate(header_norm):
                if ("libelle" in h or "libellé" in h) and "copro" not in h:
                    col_idx.setdefault("libelle", i)
                elif "copropri" in h:
                    col_idx.setdefault("owner", i)
                elif "total" in h and ("quotit" in h or "qt" in h):
                    col_idx.setdefault("qt", i)
                elif "lot" in h:
                    col_idx.setdefault("lot", i)
            # Iterate data rows; the key code/label is somewhere in the row.
            # IMPORTANT : in Optipro/Sogis PDF, each key spans TWO rows :
            #   Row N   = summary row : ['0001 - Charges communes', '-', '4', '168,00']
            #   Row N+1 = detail row  : ['Lot1\nLot2\n...', 'C0960\nC0961\n...', '-\n-...', '39\n40...']
            # We must couple them. Strategy : when we see a key-code row, register
            # the key. When we see a NO-code row, attach its lines to the LAST key.
            current_key = None  # tracks the most recently registered key dict
            def get_cell(row_, name):
                if name not in col_idx:
                    return []
                idx = col_idx[name]
                if idx >= len(row_):
                    return []
                raw_cell = row_[idx] or ""
                parts = [p.strip() for p in raw_cell.split("\n") if p.strip()]
                return parts
            for row in table[1:]:
                if not row or len(row) < 3:
                    continue
                libelles = get_cell(row, "libelle")
                owners = get_cell(row, "owner") if "owner" in col_idx else []
                lots = get_cell(row, "lot") if "lot" in col_idx else []
                qts = get_cell(row, "qt")
                if not libelles:
                    continue
                first_libelle = libelles[0]
                code_match = re.match(r"^(\d{3,4})\s*[-–]\s*(.+)$", first_libelle)
                # iter90gi : distingue SUMMARY vs DETAIL row.
                # Le regex \d{3,4} matche BOTH "0001 - Charges communes" (summary)
                # ET "001 - APPARTEMENT C2612 TEUWEN..." (detail lot).
                # Heuristique : si la cellule "coproprietaire" contient un code
                # Optipro C\d{3,5} (owner code) OU un des types de lot generiques
                # (APPARTEMENT, CAVE, PARKING, COMMERCE, STUDIO, GARAGE, BUREAU,
                # LOCAL, CHAMBRE, GRENIER), c'est une ligne de DETAIL, pas un
                # resume de cle.
                first_owner = (owners[0] if owners else "").strip()
                # Lot type patterns (case-insensitive) present in the libelle
                lot_type_patterns = (
                    "appartement", "cave", "parking", "commerce", "studio",
                    "garage", "bureau", "local", "chambre", "grenier", "atelier",
                    "loft", "duplex", "triplex", "combles",
                )
                libelle_lower = first_libelle.lower()
                looks_like_detail = (
                    re.match(r"^C\d{3,5}\b", first_owner)  # owner code Optipro
                    or any(pat in libelle_lower for pat in lot_type_patterns)
                )
                if code_match and not looks_like_detail:
                    # ---- Key SUMMARY row : open a new key ----
                    key_code = code_match.group(1)
                    key_name = code_match.group(2).strip()
                    if key_code in seen_codes:
                        current_key = None
                        continue
                    seen_codes.add(key_code)
                    # The summary row may already have lines (mixed format)
                    inline_lines = []
                    inline_total = 0.0
                    for i in range(1, max(len(libelles), len(qts))):
                        if i >= len(qts):
                            continue
                        qt = _to_float(qts[i])
                        lot_label = libelles[i] if i < len(libelles) else ""
                        owner_label = owners[i] if i < len(owners) else ""
                        lot_code = lots[i] if i < len(lots) else ""
                        if qt > 0 or lot_label:
                            inline_lines.append({
                                "lot_label": lot_label,
                                "lot_code": lot_code,
                                "owner_label": owner_label,
                                "quotity": qt,
                            })
                            inline_total += qt
                    # iter90gi : NE PAS utiliser qts[0] comme total_quotities.
                    # Sur les PDFs Optipro, la cellule qts[0] de la ligne de
                    # resume represente souvent la quotite du lot associe au
                    # code de la cle (ex : "001 APPARTEMENT" -> quotite du lot
                    # 001), et non le total de la distribution. On calcule
                    # systematiquement le total a partir des lignes de detail
                    # (une post-passe finale garantit la coherence).
                    current_key = {
                        "code": key_code,
                        "name": key_name,
                        "type": "tantiemes",
                        "lines": inline_lines,
                        "total_quotities": round(inline_total, 6),
                    }
                    keys.append(current_key)
                else:
                    # ---- DETAIL row : attach lines to the LAST opened key ----
                    if current_key is None:
                        continue
                    detail_lines = []
                    detail_total = 0.0
                    n = max(len(libelles), len(qts), len(owners))
                    for i in range(n):
                        qt = _to_float(qts[i]) if i < len(qts) else 0.0
                        lot_label = libelles[i] if i < len(libelles) else ""
                        owner_label = owners[i] if i < len(owners) else ""
                        lot_code = lots[i] if i < len(lots) else ""
                        if qt > 0 or lot_label or owner_label:
                            detail_lines.append({
                                "lot_label": lot_label,
                                "lot_code": lot_code,
                                "owner_label": owner_label,
                                "quotity": qt,
                            })
                            detail_total += qt
                    if detail_lines:
                        # If the key was registered with NO inline lines, use detail.
                        # Otherwise APPEND (mixed format support).
                        if not current_key["lines"]:
                            current_key["lines"] = detail_lines
                        else:
                            current_key["lines"].extend(detail_lines)
                        # iter90gi : total_quotities = sum(lines) systematiquement
                        # (voir commentaire sur la ligne resume ci-dessus).
                        current_key["total_quotities"] = round(
                            sum(float(l.get("quotity") or 0) for l in current_key["lines"]),
                            6,
                        )
    # iter90gi : post-passe finale - garantit que total_quotities == sum(lines)
    # pour toutes les cles (defense en profondeur contre les futures regressions
    # du parser).
    for k in keys:
        k["total_quotities"] = round(
            sum(float(l.get("quotity") or 0) for l in (k.get("lines") or [])),
            6,
        )
    # iter93r : fallback texte pour les cles au format special (ex: cle 0015
    # "Cle speciale 3/11 (A) et 8/11 (B)" dont le titre est un heading de
    # section separe et les colonnes different du format standard 5-col).
    # Pour chaque cle detectee mais sans lignes, on cherche dans le texte brut
    # les lignes de detail (format "<lot_label> C<code> - <owner_name> ...
    # <quotity_number>").
    full_text = info.get("full_text", "") or ""
    for k in keys:
        if k.get("lines"):
            continue
        code = k.get("code")
        if not code:
            continue
        # Delimite le bloc texte de la cle : de "{code} - {name}" jusqu'a la
        # prochaine cle "\d{3,4} -" ou fin de document.
        marker = f"{code} - "
        pos = full_text.find(marker)
        if pos < 0:
            continue
        # Trouve la prochaine cle
        next_key_m = re.search(r"\n\s*\d{3,4}\s*[-–]\s*[A-ZÉÈÊÀÎÔÛa-zéèêàîôû]", full_text[pos + len(marker):])
        end_pos = pos + len(marker) + next_key_m.start() if next_key_m else len(full_text)
        block = full_text[pos:end_pos]
        # Cherche les lignes de detail. Format attendu (avec variantes) :
        #   <lot_label> - <TYPE> C<code> - <owner name> - <quotity>
        # ou  <lot_label> - <TYPE> - <quotity>              (sans owner)
        # ou  <lot_label> - <TYPE> C<code> - <owner> <nb_lots> <quotity>
        # Ex : "A 001 - APPARTEMENT C0211 - Mme van den Abeele Jacqueline - 267.270000"
        # Ex : "A 301 - APPARTEMENT - 218.180000"                     (sans owner)
        # Ex : "A G01 - GARAGE C0233 - Mme DEFALQUE Tatienne - 24.550000"     (garage)
        # Ex : "A Pex7 - PARKING EXT. - 21.820000"                    (parking sans owner)
        # Ex : "B 009-010 - APPARTEMENT C0208 - M. FORSTER Sven - 400.000000" (composite)
        # Ex : "G.3-A.1.1 - APPARTEMENT C1004 - Vanrobaeys Pierre - 2859.000000" (dotted, iter93bp)
        # Ex : "G34-P21 - PARKING EXT. C0982 - Hautot Dimitri - 220.000000"     (hyphen-only, iter93bp)
        # Ex : "G.3- A.1.1 - APPARTEMENT ..."                          (espace interne, iter93bp)
        #
        # iter93z : le libelle peut etre alphanumerique (G01, Pex7) ou composite
        # (009-010). L'owner et son code C\d{3,5} sont OPTIONNELS.
        # iter93bp : le libelle peut aussi contenir des POINTS (G.3-A.1.1) et
        # etre SANS espace initial (G34-P21) ou avec espaces internes (G.3- A.1.1).
        # Nouveau pattern accepte : [A-Z][A-Za-z0-9.\- ]*? (non-greedy, autorise
        # dots, hyphens, whitespaces internes).
        detail_pat = re.compile(
            r"^(?P<libelle>[A-Z][A-Za-z0-9.\- ]*?)\s+[-–]\s+"
            r"(?P<type>[A-Z][A-ZÀ-Ÿ .]+?)"
            r"(?:\s+(?P<owner>C\d{3,5}\s*[-–]\s*[^\n]+?))?"
            r"\s+(?:[-–]|\d+)\s+"
            r"(?P<qt>\d+[.,]\d+)\s*$",
            re.MULTILINE,
        )
        recovered = []
        for m in detail_pat.finditer(block):
            qt = _to_float(m.group("qt"))
            if qt <= 0:
                continue
            owner_grp = m.group("owner")
            recovered.append({
                "lot_label": m.group("libelle").strip(),
                "lot_code": "",
                "owner_label": (owner_grp or "").strip(),
                "quotity": qt,
            })
        if recovered:
            k["lines"] = recovered
            k["total_quotities"] = round(sum(float(l["quotity"]) for l in recovered), 6)
    # Fallback : if no key parsed from tables, attempt text-based extraction
    if not keys:
        text = info.get("full_text", "")
        key_blocks = re.split(r"(?=^\d{3,4}\s*[-–]\s*[A-Z])", text, flags=re.MULTILINE)
        for block in key_blocks:
            kmatch = re.match(r"^(\d{3,4})\s*[-–]\s*(.+?)$", block.strip().splitlines()[0] if block.strip() else "")
            if not kmatch:
                continue
            keys.append({
                "code": kmatch.group(1),
                "name": kmatch.group(2).strip(),
                "type": "tantiemes",
                "lines": [],
                "total_quotities": 0.0,
            })
    return {"keys": keys, "count": len(keys)}


def _to_float(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0



# ============================================================
# OD YEAR-END ENTRIES PDF parser (Optipro "Liste des depenses")
# ============================================================
# Extracts only the rows where N° piece = "-" (Operations Diverses).
# Skips compte 650 (Frais bancaires) as those are handled by FI journals.
def parse_od_entries_pdf(raw: bytes) -> dict:
    """Auto-detect and parse OD entries from either :
      A) Optipro "Liste des depenses" PDF (rows with N° piece = "-")
      B) Optipro "Journal comptable - OD" PDF (full balanced double-entries)

    Returns a unified shape :
    {
      format: "expense_list" | "od_journal",
      entries: [...],  # shape depends on format
      total_count, total_amount, period_start, period_end
    }
    """
    # Detection : peek at first page for "JOURNAL COMPTABLE" header
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            first_txt = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    except Exception:
        first_txt = ""
    is_journal_od = (
        "JOURNAL COMPTABLE" in first_txt.upper()
        and "OD" in first_txt.upper()
        and "OP" in first_txt.upper()  # OPERATIONS / OPÉRATIONS
    )
    if is_journal_od:
        return _parse_od_journal_pdf(raw)
    # Fallback : Liste des depenses parser
    return _parse_od_expense_list_pdf(raw)


def _parse_od_journal_pdf(raw: bytes) -> dict:
    """Parse Optipro 'Journal comptable - OD' PDF (full balanced double-entries).

    Format :
      Entry header : "DD/MM/YYYY - NNNNNN - description TOTAL_DEB TOTAL_CRED"
      Each line   : "ACCOUNT - LIBELLE [| AUX_INFO] OD DD/MM/YYYY DD/MM/YYYY DEBIT CREDIT"

    Returns: {
      format: "od_journal",
      entries: [{date, reference, description, lines: [...], total_debit,
                 total_credit, balanced, included, exclusion_reason}],
      ...
    }
    """
    import re
    info = {
        "format": "od_journal",
        "entries": [],
        "total_count": 0,
        "total_amount": 0.0,
        "period_start": "",
        "period_end": "",
    }
    header_re = re.compile(r"^(\d{2}/\d{2}/\d{4}) - (\d+) - (.+?) ([\d ]+,\d{2}) ([\d ]+,\d{2})$")
    line_re = re.compile(r"^(\d{2,8}) - (.+?) OD (\d{2}/\d{2}/\d{4}) (\d{2}/\d{2}/\d{4}) ([\d ]+,\d{2}) ([\d ]+,\d{2})$")
    period_re = re.compile(r"DU (\d{2}/\d{2}/\d{4}) AU (\d{2}/\d{2}/\d{4})")

    def _f(s: str) -> float:
        try:
            return float(s.replace(" ", "").replace("\u00a0", "").replace(",", "."))
        except (ValueError, TypeError):
            return 0.0

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        all_lines = []
        for pi, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            for ln in txt.split("\n"):
                all_lines.append((pi + 1, ln))
        # period detection
        for _pi, ln in all_lines[:10]:
            m = period_re.search(ln.upper().replace("É", "E"))
            if m:
                info["period_start"] = m.group(1)
                info["period_end"] = m.group(2)
                break

    current = None
    for pi, ln in all_lines:
        ln_strip = ln.strip()
        if not ln_strip:
            continue
        mh = header_re.match(ln_strip)
        if mh:
            if current:
                info["entries"].append(current)
            desc = mh.group(3).strip()
            # Closing entries should be excluded by default : they transfer all
            # expense accounts to 701 (provisions) and split among owners. If we
            # imported them too, we'd duplicate the year's charges.
            # Pattern : description STARTS with "Cloture - " or "Clôture - "
            # (real closing accounting entries always have this prefix from
            # Optipro). Sinistre closures like "Sinistre X - clôture" are NOT
            # accounting closings and should be imported.
            desc_low = desc.lower()
            is_closing = (
                desc_low.startswith("cloture - ")
                or desc_low.startswith("clôture - ")
                or desc_low.startswith("solde des comptes")
            )
            current = {
                "date": _to_iso_date(mh.group(1)),
                "date_display": mh.group(1),
                "reference": mh.group(2),
                "description": desc,
                "total_debit": _f(mh.group(4)),
                "total_credit": _f(mh.group(5)),
                "lines": [],
                "balanced": True,
                "included": not is_closing,
                "exclusion_reason": "Ecriture de cloture (eviterait les doublons)" if is_closing else "",
                "source_page": pi,
            }
            continue
        ml = line_re.match(ln_strip)
        if ml and current:
            account = ml.group(1)
            label_full = ml.group(2).strip()
            label = label_full
            aux_info = ""
            if " | " in label_full:
                label, aux_info = label_full.split(" | ", 1)
                aux_info = aux_info.strip()
            current["lines"].append({
                "account_number": account,
                "account_name": label.strip(),
                "auxiliary_info": aux_info,
                "debit": _f(ml.group(5)),
                "credit": _f(ml.group(6)),
            })
    if current:
        info["entries"].append(current)

    # Validate balance per entry
    for e in info["entries"]:
        sum_d = round(sum(l["debit"] for l in e["lines"]), 2)
        sum_c = round(sum(l["credit"] for l in e["lines"]), 2)
        e["balanced"] = abs(sum_d - sum_c) < 0.01
        if not e["balanced"]:
            e["included"] = False
            e["exclusion_reason"] = f"Ecriture desequilibree (D={sum_d} C={sum_c})"

    info["total_count"] = len(info["entries"])
    info["total_amount"] = round(sum(e["total_debit"] for e in info["entries"] if e["included"]), 2)
    return info


def _parse_od_expense_list_pdf(raw: bytes) -> dict:
    """Parse Optipro 'Liste des depenses' PDF and extract only OD entries
    (rows where Ref. interne = '-'). Skips bank fees (compte 650).

    OD entries = year-end adjustments :
      - Charges a reporter / Annulation charges a reporter
      - Factures a recevoir (FAR)
      - Nettoyage de bilan (AGS)
      - Sinistre adjustments (cloture, regularisation)
      - Imputation coproprietaire (frais privatifs)
      - Corrections de situation de compte

    Column boundaries (Optipro standard 'Liste des depenses') :
      Date : x ~ 50-100
      Libelle : x ~ 110-340
      Fournisseur : x ~ 341-460
      Ref. interne : x ~ 460-555  (OD if = "-")
      Montant : x ~ 625-680
      Part proprietaire : x ~ 695-735
      Part occupant : x ~ 765-800

    Returns: {
      entries: [{date, libelle, account_number, account_name,
                 amount (signed), proprietaire_pct, occupant_pct,
                 suggested_counterpart, source_page, source_y}],
      total_count, total_amount, period_start, period_end
    }
    """
    import re
    info = {
        "format": "expense_list",
        "entries": [],
        "total_count": 0,
        "total_amount": 0.0,
        "period_start": "",
        "period_end": "",
    }
    date_re = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    amount_re = re.compile(r"^-?[\d.,\u00a0 ]+$")
    compte_re = re.compile(r"^(\d{3,10})$")
    period_re = re.compile(r"^(\d{2}/\d{2}/\d{4})$")

    def _to_float(parts: list[str]) -> float:
        """Join multi-token amount '1 234,56' -> 1234.56, '-3 183,65' -> -3183.65."""
        joined = "".join(parts).replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            return float(joined)
        except (ValueError, TypeError):
            return 0.0

    def _is_libelle_word(w) -> bool:
        return 100 < w["x0"] < 340

    def _is_supplier_zone(w) -> bool:
        return 340 < w["x0"] < 455

    def _is_ref_zone(w) -> bool:
        return 455 < w["x0"] < 555

    def _is_amount_zone(w) -> bool:
        return 620 < w["x0"] < 685

    def _is_prop_zone(w) -> bool:
        return 685 < w["x0"] < 745

    def _is_occ_zone(w) -> bool:
        return 745 < w["x0"] < 810

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        # Period detection (page 1 header)
        if pdf.pages:
            p0 = pdf.pages[0]
            ws0 = p0.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            for w in ws0:
                if w["top"] < 60:
                    m = period_re.match(w["text"])
                    if m:
                        if not info["period_start"]:
                            info["period_start"] = m.group(1)
                        else:
                            info["period_end"] = m.group(1)

        # current_account = the most recent "Compte : XXXXX - LIBELLE" seen above
        current_account = ""
        current_account_name = ""

        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
            if not words:
                continue
            # Group by line (round y to nearest)
            from collections import defaultdict
            lines: dict[float, list] = defaultdict(list)
            for w in words:
                lines[round(w["top"])].append(w)

            for y in sorted(lines.keys()):
                ws = sorted(lines[y], key=lambda w: w["x0"])
                # ---- Detect "Compte : XXXXX - LIBELLE" header rows -----
                # Pattern : ['Compte', ':', '61011', '-', 'Contrat', ...]
                if (len(ws) >= 4
                    and ws[0]["text"] == "Compte"
                    and ws[1]["text"] == ":"
                    and compte_re.match(ws[2]["text"])):
                    current_account = ws[2]["text"]
                    # libelle = everything after the dash, before amounts (x < 620)
                    lbl_parts = []
                    for w in ws[3:]:
                        if w["text"] == "-":
                            continue
                        if w["x0"] > 620:
                            break
                        lbl_parts.append(w["text"])
                    current_account_name = " ".join(lbl_parts).strip()
                    continue

                # ---- Detect a data row : starts with a date ----
                if not ws or not date_re.match(ws[0]["text"]) or ws[0]["x0"] > 80:
                    continue
                date_str = ws[0]["text"]
                # Parse Ref. interne column : must be "-" for OD
                ref_ws = [w for w in ws if _is_ref_zone(w)]
                ref_text = " ".join(w["text"] for w in ref_ws).strip()
                if ref_text != "-":
                    continue  # invoice row, not OD

                # Filter : skip if no current_account context (header glitch)
                if not current_account:
                    continue

                # Filter : skip bank fees (compte 650) - they belong to FI journals
                if current_account == "650":
                    continue

                # Libelle : all words in x=100-340 zone
                lib_words = [w for w in ws if _is_libelle_word(w)]
                lib_text = " ".join(w["text"] for w in sorted(lib_words, key=lambda w: w["x0"]))

                # Supplier text (usually "-" for OD but keep for context)
                sup_words = [w for w in ws if _is_supplier_zone(w)]
                sup_text = " ".join(w["text"] for w in sup_words).strip()
                if sup_text and sup_text != "-":
                    lib_text = (lib_text + " | " + sup_text).strip(" |")

                # Montant (signed) : x=620-685
                amt_words = [w for w in ws if _is_amount_zone(w)]
                amt_parts = [w["text"] for w in sorted(amt_words, key=lambda w: w["x0"])]
                amount = _to_float(amt_parts)
                if abs(amount) < 0.005:
                    continue  # zero-amount, skip

                # Part proprietaire : x=685-745
                prop_words = [w for w in ws if _is_prop_zone(w)]
                prop_amount = _to_float([w["text"] for w in sorted(prop_words, key=lambda w: w["x0"])])
                # Part occupant : x=745-810
                occ_words = [w for w in ws if _is_occ_zone(w)]
                occ_amount = _to_float([w["text"] for w in sorted(occ_words, key=lambda w: w["x0"])])
                total_pct = 100.0
                if abs(amount) > 0.01:
                    prop_pct = round(abs(prop_amount) / abs(amount) * 100, 2)
                    occ_pct = round(abs(occ_amount) / abs(amount) * 100, 2)
                else:
                    prop_pct, occ_pct = 100.0, 0.0
                # Sanity: clip
                prop_pct = max(0.0, min(100.0, prop_pct))
                occ_pct = max(0.0, min(100.0, occ_pct))

                info["entries"].append({
                    "date": _to_iso_date(date_str),
                    "libelle": lib_text.strip(),
                    "account_number": current_account,
                    "account_name": current_account_name,
                    "amount": round(amount, 2),
                    "proprietaire_pct": prop_pct,
                    "occupant_pct": occ_pct,
                    "suggested_counterpart": _suggest_od_counterpart(lib_text, current_account, amount),
                    "source_page": page_idx + 1,
                    "source_y": y,
                })
                info["total_amount"] += amount

    info["entries"].sort(key=lambda e: (e["date"], e["account_number"]))
    info["total_count"] = len(info["entries"])
    info["total_amount"] = round(info["total_amount"], 2)
    return info


def _to_iso_date(s: str) -> str:
    """DD/MM/YYYY -> YYYY-MM-DD"""
    try:
        parts = s.split("/")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    except (ValueError, IndexError):
        pass
    return s


def _suggest_od_counterpart(libelle: str, charge_account: str, amount: float) -> dict:
    """Suggest counterpart account for an OD entry based on libelle keywords.

    Returns {"account": "XXX", "account_name": "...", "confidence": "high/medium/low/none"}.
    `confidence=none` means user MUST choose manually.
    """
    if not libelle:
        # Even without libelle, special : compte 643 (Frais privatifs) -> 410
        if charge_account == "643":
            return {"account": "410", "account_name": "Coproprietaires", "confidence": "high"}
        return {"account": "", "account_name": "", "confidence": "none"}
    # Special : compte 643 (Frais privatifs) is ALWAYS paired with 410 (imputation
    # coproprietaire) regardless of libelle keywords - check this first.
    if charge_account == "643":
        return {"account": "410", "account_name": "Coproprietaires", "confidence": "high"}
    # Normalize accents for keyword matching
    import unicodedata
    lbl_raw = libelle.lower().strip()
    lbl = "".join(ch for ch in unicodedata.normalize("NFD", lbl_raw) if unicodedata.category(ch) != "Mn")

    # Order matters : more specific patterns first
    rules = [
        # FAR : Factures a recevoir
        (("far ", "facture a recevoir", "factures a recevoir"), "444", "Factures a recevoir", "high"),
        # Charges a reporter (deferral)
        (("annulation charges a reporter", "annulation des charges a reporter"),
         "490", "Charges a reporter", "high"),
        (("charge a reporter", "charges a reporter"), "490", "Charges a reporter", "high"),
        # AGS / Nettoyage de bilan -> creances douteuses
        (("nettoyage de bilan", "ags point", "ags decision", "apurement creance"),
         "417", "Creances douteuses", "high"),
        # SIN INONDATION specific
        (("sin 202200724", "sin202200724", "regularisation sin 202200724"),
         "494001", "SIN 202200724 INONDATION", "high"),
        # Sinistre inondation / Sinistre canalisation -> SIN INONDATION generic
        (("sin ", "regularisation sin", "sinistre canalisation"),
         "494001", "SIN INONDATION", "medium"),
        # Sinistre pompe / Pompe de relevage
        (("sinistre pompe", "pompe de relevage", "sinistre garage"),
         "499603", "Sinistre garage - Pompe de relevage", "high"),
        # Sinistre inondation pompe
        (("sinistre innondation", "sinistre inondation"),
         "499603", "Sinistre garage - Pompe de relevage", "medium"),
        # Generic sinistre
        (("sinistre", "rupture devidoir", "remboursement sinistre"),
         "4990", "Provisions sinistres diverses", "low"),
        # Imputation coproprietaire / Frais privatifs
        (("imputation coproprietaire", "imputation proprietaire", "imputation occupant"),
         "410", "Coproprietaires", "high"),
        # Correction de situation de compte
        (("correction de situation de compte", "correction situation",
          "transfert solde crediteur", "transfert solde debiteur"),
         "410", "Coproprietaires", "medium"),
        # Refunds / Reimbursements (negative entries on 61066 typically)
        (("remboursement",), "4990", "Provisions sinistres diverses", "low"),
    ]

    for keywords, account, account_name, confidence in rules:
        for kw in keywords:
            if kw in lbl:
                return {"account": account, "account_name": account_name, "confidence": confidence}

    # Special : if compte 643 (Frais privatifs) -> counterpart is always 410
    # (already handled at top of function, kept here for safety)

    return {"account": "", "account_name": "", "confidence": "none"}
