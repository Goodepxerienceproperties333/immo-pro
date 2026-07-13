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

/**
 * Normalise un nom de fournisseur :
 * - minuscules
 * - split en mots
 * - filtre les particules juridiques (sa, sprl, srl, bvba, ...)
 * - retire les caracteres non-alphanumeriques restants dans chaque mot
 * - trie alphabetiquement pour matcher "Finlead SRL" et "SRL Finlead"
 * Si apres filtrage il ne reste rien, on retombe sur les mots d'origine
 * (garde-fou pour un input tel que "SRL" tout seul).
 */
export function normSupplierName(value) {
  const words = (value || '').toString().trim().toLowerCase().split(/\s+/).filter(Boolean);
  const filtered = words.filter(w => !isParticle(w));
  const src = filtered.length ? filtered : words;
  // On retire aussi la ponctuation interne des mots restants pour matcher
  // "Finlead." et "Finlead" identiquement.
  const cleaned = src.map(w => w.replace(/[^a-z0-9]/g, '')).filter(Boolean);
  return cleaned.sort().join(' ');
}
