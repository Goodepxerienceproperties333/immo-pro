/**
 * Hook utilitaire qui retourne les parametres `date_from` / `date_to` derives
 * du filtre global d'exercice fiscal (AuthContext > selectedFiscalYear).
 *
 * Usage :
 *   const fyParams = useFiscalYearParams();
 *   api.get('/invoices', { params: { copropriete_id, ...fyParams } });
 *
 * Si "Tous les exercices" est selectionne (selectedFiscalYearId vide), retourne {}.
 */
import { useAuth } from '@/contexts/AuthContext';

export function useFiscalYearParams() {
  const { selectedFiscalYear } = useAuth() || {};
  if (!selectedFiscalYear) return {};
  const params = {};
  if (selectedFiscalYear.start_date) params.date_from = selectedFiscalYear.start_date;
  if (selectedFiscalYear.end_date) params.date_to = selectedFiscalYear.end_date;
  return params;
}

/**
 * Retourne aussi `fiscal_year_id` qui peut etre utilise par les endpoints
 * supportant nativement le filtre par FY (decompte, bilan, resultat).
 */
export function useFiscalYearFullParams() {
  const { selectedFiscalYear, selectedFiscalYearId } = useAuth() || {};
  if (!selectedFiscalYearId || !selectedFiscalYear) return {};
  const params = { fiscal_year_id: selectedFiscalYearId };
  if (selectedFiscalYear.start_date) params.date_from = selectedFiscalYear.start_date;
  if (selectedFiscalYear.end_date) params.date_to = selectedFiscalYear.end_date;
  return params;
}
