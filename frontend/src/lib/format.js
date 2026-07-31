/**
 * Formatage numerique unifie pour toute la plateforme (iter93ab).
 *
 * Regles :
 *  - Separateur de milliers : ESPACE INSECABLE (\u00A0), conforme au standard
 *    belge/francais francophone (ex : "10 800,00").
 *  - Separateur decimal : VIRGULE (","), non le point.
 *  - Deux decimales par defaut, pour les montants EUR.
 *
 * Usage :
 *   import { fmtEUR, fmtNumber } from '@/lib/format';
 *   fmtEUR(10800.5)       -> "10 800,50"
 *   fmtEUR(-1234.5, true) -> "-1 234,50 EUR"
 *   fmtNumber(0.123456, 6) -> "0,123456"
 *
 * Les fonctions acceptent tout (string, number, undefined, null) et renvoient
 * toujours une chaine formatee. `null`/`undefined`/`NaN` -> "0,00".
 */

const _formatter2 = new Intl.NumberFormat('fr-BE', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  useGrouping: true,
});

function _toNumber(v) {
  if (v === null || v === undefined || v === '') return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Arrondit un nombre a 2 decimales, retourne un vrai `number` (pas de string).
 * Utiliser au lieu de `+fmtEUR(x)` qui produit NaN car fmtEUR retourne "x,yz"
 * (virgule fr-BE) et `+string_with_comma` = NaN.
 *
 * @param {number|string} value
 * @returns {number} - toujours un nombre fini (0 si input invalide)
 */
export function round2(value) {
  const n = _toNumber(value);
  return Math.round(n * 100) / 100;
}

/**
 * Parse une chaine formatee EUR (avec virgule et espaces) en `number`.
 * Inverse de `fmtEUR` : eur2num(fmtEUR(1234.56)) === 1234.56
 *
 * @param {string|number} value
 * @returns {number}
 */
export function eur2num(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  if (value === null || value === undefined || value === '') return 0;
  const cleaned = String(value)
    .replace(/\s|\u00A0/g, '')  // espaces (incl. insecables) supprimes
    .replace(/[^\d,.\-]/g, '')  // conserver chiffres, virgule, point, moins
    .replace(',', '.');          // virgule fr-BE -> point standard JS
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : 0;
}



/**
 * Formate un montant EUR avec espace comme separateur de milliers et virgule
 * comme separateur decimal. Toujours 2 decimales.
 * @param {number|string} value
 * @param {boolean} [withCurrency=false] Ajoute " EUR" a la fin
 * @returns {string}
 */
export function fmtEUR(value, withCurrency = false) {
  const n = _toNumber(value);
  const s = _formatter2.format(n);
  return withCurrency ? `${s} EUR` : s;
}

/**
 * Formate un nombre avec espace comme separateur de milliers et virgule comme
 * separateur decimal. Le nombre de decimales est configurable.
 * @param {number|string} value
 * @param {number} [decimals=2] Nombre de decimales exact
 * @returns {string}
 */
export function fmtNumber(value, decimals = 2) {
  const n = _toNumber(value);
  const fmt = new Intl.NumberFormat('fr-BE', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: true,
  });
  return fmt.format(n);
}

/**
 * Formate une quotite / part (jusqu'a 6 decimales, sans zeros inutiles).
 * Utile pour les cles de repartition (ex : "800", "267,270000" -> "267,27").
 * @param {number|string} value
 * @returns {string}
 */
export function fmtQuotity(value) {
  const n = _toNumber(value);
  const fmt = new Intl.NumberFormat('fr-BE', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
    useGrouping: true,
  });
  return fmt.format(n);
}

/**
 * Formate un pourcentage (0-100).
 * @param {number|string} value
 * @param {number} [decimals=2]
 * @returns {string}
 */
export function fmtPct(value, decimals = 2) {
  return `${fmtNumber(value, decimals)} %`;
}
