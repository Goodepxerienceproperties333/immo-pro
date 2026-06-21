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
    """Parse 'Liste des natures de depense' PDF from Optipro.

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
# BUDGET PDF parser
# ============================================================
def parse_budget_pdf(raw: bytes) -> dict:
    """Parse 'Budget' PDF from Optipro.

    Structure observed : sections grouped by distribution key code (e.g. "0001 -
    Charges communes"). Each section lists PCMN accounts with budgeted amounts.

    Returns: { sections: [{ key_code, key_label, lines: [{account, libelle, amount}] }],
               total_global, count }
    """
    info = extract_pdf(raw)
    sections: list[dict] = []
    current_section: dict = None
    total_global = 0.0

    # Strategy : iterate the full text line by line and use regex to detect
    # - section headers : starts with 4-digit code, e.g. "0001 - Charges communes"
    # - data lines : starts with a PCMN account number (3-7 digits) + libelle + amount
    import re
    full_text = info.get("full_text", "")
    for raw_line in full_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        # Section header : "0001 - Libelle"
        sec_match = re.match(r"^(\d{4})\s*[-–]\s*(.+?)(?:\s+\d[\d\s.,]*)?$", line)
        # We accept the section header only if it doesn't look like a data line
        if sec_match and not re.search(r"\d+[.,]\d{2}\s*$", line):
            code = sec_match.group(1)
            label = sec_match.group(2).strip()
            current_section = {"key_code": code, "key_label": label, "lines": [], "subtotal": 0.0}
            sections.append(current_section)
            continue
        # Data line : account + libelle + amount at the end
        # Account is 3-7 digits, can be 6 or 7 digits (612590)
        # Amount ends with "x.xx" or "-x.xx"
        dl_match = re.match(r"^(\d{3,7})\s+(.+?)\s+(-?[\d\s]+[.,]\d{2})\s*$", line)
        if dl_match and current_section is not None:
            account = dl_match.group(1)
            libelle = dl_match.group(2).strip()
            amount = _to_float(dl_match.group(3).replace(" ", "").replace(",", "."))
            current_section["lines"].append({
                "account": account,
                "libelle": libelle,
                "amount": amount,
            })
            current_section["subtotal"] += amount
            total_global += amount
            continue
        # "Total" line at the end : extract the total_global
        total_match = re.search(r"total[^\d]*(-?[\d\s]+[.,]\d{2})", line.lower())
        if total_match and "section" not in line.lower():
            tg = _to_float(total_match.group(1).replace(" ", "").replace(",", "."))
            if tg > total_global:
                total_global = tg
    return {
        "sections": sections,
        "total_global": round(total_global, 2),
        "count": sum(len(s["lines"]) for s in sections),
    }


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
            # Iterate data rows; the key code/label is somewhere in the row
            for row in table[1:]:
                if not row or len(row) < 3:
                    continue
                # Try to split each cell by `\n` similar to natures parser
                def get_cell(name):
                    if name not in col_idx:
                        return []
                    idx = col_idx[name]
                    if idx >= len(row):
                        return []
                    raw_cell = row[idx] or ""
                    parts = [p.strip() for p in raw_cell.split("\n") if p.strip()]
                    return parts
                libelles = get_cell("libelle")
                owners = get_cell("owner") if "owner" in col_idx else []
                lots = get_cell("lot") if "lot" in col_idx else []
                qts = get_cell("qt")
                # The first libelle is typically the KEY name (e.g. "0001 - Charges communes")
                if not libelles:
                    continue
                first_libelle = libelles[0]
                code_match = re.match(r"^(\d{3,4})\s*[-–]\s*(.+)$", first_libelle)
                # If no key code at the top of the row -> skip (not a new key, just a continuation)
                if not code_match:
                    continue
                key_code = code_match.group(1)
                key_name = code_match.group(2).strip()
                if key_code in seen_codes:
                    continue
                seen_codes.add(key_code)
                # Lines : remaining libelles paired with quotities and owners/lots
                lines = []
                total_qt = 0.0
                for i in range(1, max(len(libelles), len(qts))):
                    if i >= len(qts):
                        continue
                    qt_str = qts[i]
                    qt = _to_float(qt_str)
                    lot_label = libelles[i] if i < len(libelles) else ""
                    owner_label = owners[i] if i < len(owners) else ""
                    lot_code = lots[i] if i < len(lots) else ""
                    if qt > 0 or lot_label:
                        lines.append({
                            "lot_label": lot_label,
                            "lot_code": lot_code,
                            "owner_label": owner_label,
                            "quotity": qt,
                        })
                        total_qt += qt
                # Last quotity in the row could be the total (e.g. 168.00 vs sum of 39+40+49+40)
                if qts:
                    last_qt = _to_float(qts[0])
                    if abs(last_qt - total_qt) > 0.01 and last_qt > 0:
                        # Use the explicit total if available
                        explicit_total = last_qt
                    else:
                        explicit_total = total_qt
                else:
                    explicit_total = total_qt
                keys.append({
                    "code": key_code,
                    "name": key_name,
                    "type": "tantiemes",
                    "lines": lines,
                    "total_quotities": round(explicit_total, 6),
                })
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
