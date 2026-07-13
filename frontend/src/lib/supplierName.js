// iter90fa : normalisation partagee des noms de fournisseurs.
// DOIT rester equivalent a `_norm_name` dans backend/routes/suppliers.py
// pour eviter les divergences PC / serveur.

const LEGAL_PARTICLES = new Set([
  'sa', 'sprl', 'srl', 'sarl', 'sas', 'scrl', 'asbl', 'scs', 'snc',
  'nv', 'bv', 'bvba', 'cvba', 'vzw', 'sc', 'sca', 'sepa',
  'sci', 'gmbh', 'ag', 'ltd', 'llc', 'inc',
]);

const isParticle = (w) => {
  const core = w.replace(/[^a-z0-9]/g, '');
  return LEGAL_PARTICLES.has(core);
};

function _norm(words) {
  const filtered = words.filter(w => !isParticle(w));
  const src = filtered.length ? filtered : words;
  const cleaned = src.map(w => w.replace(/[^a-z0-9]/g, '')).filter(Boolean);
  return cleaned.sort().join(' ');
}

/**
 * Normalise un nom de fournisseur :
 * - retire d'abord tout contenu entre parentheses
 * - minuscules, split en mots, filtre particules juridiques, tri
 * Ex: "Finlead Properties (Finlead srl)" -> "finlead properties"
 */
export function normSupplierName(value) {
  const stripped = (value || '').toString().replace(/\([^)]*\)/g, ' ');
  const words = stripped.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return _norm(words);
}

/**
 * iter90fb : retourne toutes les cles candidates pour un matching. Chaque
 * fragment entre parentheses genere une cle autonome. Permet de matcher
 * "Finlead Properties (Finlead srl)" avec la fiche "Finlead SRL".
 */
export function normSupplierNameCandidates(value) {
  const set = new Set();
  const primary = normSupplierName(value);
  if (primary) set.add(primary);
  const re = /\(([^)]+)\)/g;
  let m;
  const s = (value || '').toString();
  while ((m = re.exec(s)) !== null) {
    const inner = m[1];
    const words = inner.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const norm = _norm(words);
    if (norm) set.add(norm);
  }
  return set;
}
