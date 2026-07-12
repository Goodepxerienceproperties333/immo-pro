import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { ArrowLeft, Unlock, Trash2, RotateCcw, AlertTriangle, Search } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

/**
 * Outils de deblocage comptable reserves au superadmin.
 *  - Rechercher une ecriture (par ACP, date, ref, description)
 *  - Forcer la modification d'une ecriture (meme si FY cloture)
 *  - Forcer la suppression d'une ecriture (avec backup dans deleted_entries)
 *  - Forcer la reouverture d'un exercice fiscal
 * Toutes les actions tracees dans audit_log.
 */
export default function AdminUnlockEntryPage() {
  const [copros, setCopros] = useState([]);
  const [fys, setFys] = useState([]);
  const [filters, setFilters] = useState({
    copropriete_id: '__all__', date_from: '', date_to: '', q: '', journal_type: '__all__',
  });
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [actionDialog, setActionDialog] = useState(null); // 'edit' | 'delete' | 'reopen'
  const [reason, setReason] = useState('');
  const [editPatch, setEditPatch] = useState({});

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get('/coproprietes?include_archived=true');
        setCopros(data);
      } catch { /* noop */ }
    })();
  }, []);

  useEffect(() => {
    if (filters.copropriete_id && filters.copropriete_id !== '__all__') {
      (async () => {
        try {
          const { data } = await api.get('/fiscal/years', { params: { copropriete_id: filters.copropriete_id } });
          setFys(data);
        } catch { setFys([]); }
      })();
    } else {
      setFys([]);
    }
  }, [filters.copropriete_id]);

  const search = async () => {
    setLoading(true);
    try {
      const params = {};
      if (filters.copropriete_id !== '__all__') params.copropriete_id = filters.copropriete_id;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.q) params.q = filters.q;
      if (filters.journal_type !== '__all__') params.journal_type = filters.journal_type;
      const { data } = await api.get('/admin/locked-entries-search', { params });
      setEntries(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur recherche');
    } finally {
      setLoading(false);
    }
  };

  const openAction = (entry, action) => {
    setSelected(entry);
    setActionDialog(action);
    setReason('');
    if (action === 'edit') {
      setEditPatch({
        date: entry.date || '',
        description: entry.description || '',
        reference: entry.reference || '',
      });
    }
  };

  const submitEdit = async () => {
    if (!reason || reason.length < 5) {
      toast.error('Justification requise (min 5 caracteres)');
      return;
    }
    try {
      await api.post(`/admin/unlock-entry/${selected.id}`, { patch: editPatch, reason });
      toast.success('Ecriture deverrouillee et modifiee');
      setActionDialog(null);
      search();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const submitDelete = async () => {
    if (!reason || reason.length < 10) {
      toast.error('Justification detaillee requise (min 10 caracteres)');
      return;
    }
    try {
      await api.delete(`/admin/entries/${selected.id}/force`, { data: { reason } });
      toast.success('Ecriture supprimee (backup conserve)');
      setActionDialog(null);
      search();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const submitReopen = async (fyId) => {
    if (!reason || reason.length < 10) {
      toast.error('Justification detaillee requise (min 10 caracteres)');
      return;
    }
    try {
      await api.post(`/admin/force-reopen-fy/${fyId}`, { reason });
      toast.success('Exercice force a "open"');
      setActionDialog(null);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  return (
    <div className="space-y-4" data-testid="admin-unlock-page">
      <div className="flex items-center gap-3">
        <Link to="/admin" className="text-[#022D52] hover:underline text-sm flex items-center gap-1" data-testid="back-to-admin">
          <ArrowLeft size={14} /> Retour Admin
        </Link>
      </div>
      <div className="page-header">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-amber-100 flex items-center justify-center text-amber-700"><Unlock size={20} /></div>
          <div>
            <h1 className="page-title">Outils de deblocage comptable</h1>
            <p className="page-subtitle text-amber-700">Actions critiques - chaque modification est tracee dans le journal d&apos;audit.</p>
          </div>
        </div>
      </div>

      {/* Force reopen FY block */}
      {filters.copropriete_id !== '__all__' && fys.length > 0 && (
        <Card className="border-amber-200" data-testid="card-force-reopen">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><RotateCcw size={16} className="text-amber-600" />Forcer la reouverture d&apos;un exercice</CardTitle></CardHeader>
          <CardContent>
            <p className="text-xs text-slate-600 mb-2">A utiliser UNIQUEMENT si le reopen normal echoue. Ne genere PAS de contre-passation automatique.</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {fys.map(fy => (
                <div key={fy.id} className="flex items-center justify-between border border-slate-200 rounded p-2">
                  <div className="text-sm">
                    <span className="font-semibold">{fy.name}</span>{' '}
                    <Badge variant={fy.status === 'closed' ? 'destructive' : 'outline'} className="ml-1">{fy.status}</Badge>
                    <div className="text-xs text-slate-500">{fmtDate(fy.start_date)} au {fmtDate(fy.end_date)}</div>
                  </div>
                  {fy.status === 'closed' && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-amber-700 border-amber-300"
                      onClick={() => { setSelected({ fy_id: fy.id, fy_name: fy.name }); setActionDialog('reopen'); setReason(''); }}
                      data-testid={`force-reopen-${fy.id}`}
                    >
                      <RotateCcw size={12} className="mr-1" /> Forcer
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Search block */}
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><Search size={16} />Recherche d&apos;ecritures</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            <Select value={filters.copropriete_id} onValueChange={v => setFilters({...filters, copropriete_id: v})}>
              <SelectTrigger className="text-sm" data-testid="filter-copro"><SelectValue placeholder="ACP" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Toutes les ACPs</SelectItem>
                {copros.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={filters.journal_type} onValueChange={v => setFilters({...filters, journal_type: v})}>
              <SelectTrigger className="text-sm" data-testid="filter-jtype"><SelectValue placeholder="Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Tous types</SelectItem>
                <SelectItem value="VE">VE - Ventes</SelectItem>
                <SelectItem value="AC">AC - Achats</SelectItem>
                <SelectItem value="FI">FI - Banque</SelectItem>
                <SelectItem value="OD">OD - Operations diverses</SelectItem>
                <SelectItem value="AN">AN - A-nouveau</SelectItem>
              </SelectContent>
            </Select>
            <Input type="date" value={filters.date_from} onChange={e => setFilters({...filters, date_from: e.target.value})} placeholder="Du" data-testid="filter-from" />
            <Input type="date" value={filters.date_to} onChange={e => setFilters({...filters, date_to: e.target.value})} placeholder="Au" data-testid="filter-to" />
            <Input value={filters.q} onChange={e => setFilters({...filters, q: e.target.value})} placeholder="Ref / description..." data-testid="filter-q" />
          </div>
          <Button onClick={search} disabled={loading} className="mt-3 bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="search-btn">
            <Search size={14} className="mr-2" /> {loading ? 'Recherche...' : 'Rechercher'}
          </Button>
        </CardContent>
      </Card>

      {/* Results */}
      {entries.length > 0 && (
        <Card data-testid="results">
          <CardHeader className="pb-2"><CardTitle className="text-base">{entries.length} ecriture{entries.length > 1 ? 's' : ''} trouvee{entries.length > 1 ? 's' : ''}</CardTitle></CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-slate-600 text-xs uppercase">
                  <tr>
                    <th className="px-2 py-2 text-left">Date</th>
                    <th className="px-2 py-2">Type</th>
                    <th className="px-2 py-2 text-left">Reference</th>
                    <th className="px-2 py-2 text-left">Description</th>
                    <th className="px-2 py-2 text-right">Debit</th>
                    <th className="px-2 py-2">Exercice</th>
                    <th className="px-2 py-2">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {entries.map(e => (
                    <tr key={e.id} className="hover:bg-slate-50" data-testid={`entry-row-${e.id}`}>
                      <td className="px-2 py-2 font-mono text-xs">{fmtDate(e.date)}</td>
                      <td className="px-2 py-2 text-center"><Badge variant="outline" className="text-xs">{e.journal_type}</Badge></td>
                      <td className="px-2 py-2 font-mono text-xs">{e.reference}</td>
                      <td className="px-2 py-2">
                        {e.description}
                        {e.is_reversal && <Badge className="ml-1 bg-orange-100 text-orange-800 border-orange-300 text-[10px]">REVERSAL</Badge>}
                        {e.reversed && <Badge className="ml-1 bg-red-100 text-red-800 border-red-300 text-[10px]">EXTOURNE</Badge>}
                        {e.unlocked_by_admin && <Badge className="ml-1 bg-amber-100 text-amber-800 border-amber-300 text-[10px]">UNLOCK</Badge>}
                      </td>
                      <td className="px-2 py-2 text-right font-mono">{(e.total_debit || 0).toFixed(2)}</td>
                      <td className="px-2 py-2 text-center text-xs">
                        {e.fy_name}{' '}
                        <Badge variant={e.fy_status === 'closed' ? 'destructive' : 'outline'} className="ml-1 text-[10px]">{e.fy_status}</Badge>
                      </td>
                      <td className="px-2 py-2">
                        <div className="flex gap-1 justify-center">
                          <Button variant="outline" size="sm" onClick={() => openAction(e, 'edit')} data-testid={`btn-edit-${e.id}`} className="h-7 px-2 text-amber-700 border-amber-300">
                            <Unlock size={12} />
                          </Button>
                          <Button variant="outline" size="sm" onClick={() => openAction(e, 'delete')} data-testid={`btn-delete-${e.id}`} className="h-7 px-2 text-red-700 border-red-300">
                            <Trash2 size={12} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Edit dialog */}
      <Dialog open={actionDialog === 'edit'} onOpenChange={(o) => !o && setActionDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle className="flex items-center gap-2"><Unlock size={16} className="text-amber-600" /> Modifier une ecriture verrouillee</DialogTitle></DialogHeader>
          <div className="space-y-3 text-sm">
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-amber-900 text-xs">
              <AlertTriangle size={14} className="inline mr-1" />
              Cette ecriture appartient {selected?.fy_status === 'closed' ? 'a un exercice CLOTURE' : 'a un exercice ouvert'}.
              La modification sera trace dans l&apos;audit log.
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Date</label>
              <Input type="date" value={editPatch.date || ''} onChange={e => setEditPatch({...editPatch, date: e.target.value})} data-testid="edit-date" />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Reference</label>
              <Input value={editPatch.reference || ''} onChange={e => setEditPatch({...editPatch, reference: e.target.value})} data-testid="edit-ref" />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Description</label>
              <Textarea value={editPatch.description || ''} onChange={e => setEditPatch({...editPatch, description: e.target.value})} data-testid="edit-desc" />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Justification *</label>
              <Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Pourquoi cette modification est necessaire ? (min 5 caracteres)" data-testid="edit-reason" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActionDialog(null)} data-testid="edit-cancel">Annuler</Button>
            <Button onClick={submitEdit} className="bg-amber-600 hover:bg-amber-700 text-white" data-testid="edit-submit">
              <Unlock size={14} className="mr-1" /> Deverrouiller + appliquer
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete dialog */}
      <Dialog open={actionDialog === 'delete'} onOpenChange={(o) => !o && setActionDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle className="flex items-center gap-2 text-red-700"><Trash2 size={16} /> Force-delete d&apos;une ecriture</DialogTitle></DialogHeader>
          <div className="space-y-3 text-sm">
            <div className="p-3 bg-red-50 border border-red-200 rounded text-red-900 text-xs">
              <AlertTriangle size={14} className="inline mr-1" />
              Action <b>destructrice</b>. Un backup est conserve dans `deleted_entries`. La suppression sera tracee.
            </div>
            <div className="bg-slate-50 p-2 rounded text-xs">
              <div><b>Ref:</b> {selected?.reference}</div>
              <div><b>Date:</b> {fmtDate(selected?.date)}</div>
              <div><b>Type:</b> {selected?.journal_type}</div>
              <div><b>Montant:</b> {(selected?.total_debit || 0).toFixed(2)} EUR</div>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Justification detaillee *</label>
              <Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Pourquoi supprimer cette ecriture ? (min 10 caracteres)" data-testid="del-reason" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActionDialog(null)} data-testid="del-cancel">Annuler</Button>
            <Button onClick={submitDelete} className="bg-red-600 hover:bg-red-700 text-white" data-testid="del-submit">
              <Trash2 size={14} className="mr-1" /> Supprimer
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reopen FY dialog */}
      <Dialog open={actionDialog === 'reopen'} onOpenChange={(o) => !o && setActionDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle className="flex items-center gap-2 text-amber-700"><RotateCcw size={16} /> Force-reopen de l&apos;exercice</DialogTitle></DialogHeader>
          <div className="space-y-3 text-sm">
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-amber-900 text-xs">
              <AlertTriangle size={14} className="inline mr-1" />
              Cette action passe l&apos;exercice <b>{selected?.fy_name}</b> a &quot;open&quot; <b>sans contre-passation automatique</b>.
              N&apos;utilisez que si le reopen normal echoue.
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Justification detaillee *</label>
              <Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Pourquoi forcer la reouverture ? (min 10 caracteres)" data-testid="reopen-reason" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActionDialog(null)} data-testid="reopen-cancel">Annuler</Button>
            <Button onClick={() => submitReopen(selected.fy_id)} className="bg-amber-600 hover:bg-amber-700 text-white" data-testid="reopen-submit">
              <RotateCcw size={14} className="mr-1" /> Forcer
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
