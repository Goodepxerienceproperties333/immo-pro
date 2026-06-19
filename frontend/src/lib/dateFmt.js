// Formatteur de date europeen JJ/MM/AAAA pour TOUTE l'application.
// Utiliser sur tout affichage de date (PAS sur les inputs HTML type=date).

export function fmtDate(s) {
  if (!s) return '';
  // Si deja au format JJ/MM/AAAA, on retourne tel quel
  if (typeof s === 'string' && /^\d{2}\/\d{2}\/\d{4}/.test(s)) return s.slice(0, 10);
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yy = d.getFullYear();
    return `${dd}/${mm}/${yy}`;
  } catch {
    return String(s);
  }
}

export function fmtDateTime(s) {
  if (!s) return '';
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yy = d.getFullYear();
    const h = String(d.getHours()).padStart(2, '0');
    const mn = String(d.getMinutes()).padStart(2, '0');
    return `${dd}/${mm}/${yy} ${h}:${mn}`;
  } catch {
    return String(s);
  }
}
