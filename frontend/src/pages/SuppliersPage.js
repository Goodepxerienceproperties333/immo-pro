import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, Truck, AlertTriangle } from 'lucide-react';

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name:'', vat_number:'', bce_number:'', address:'', postal_code:'', city:'', country:'Belgique', phone:'', email:'', iban:'', bic:'', default_account:'', notes:'' });
  // iter85g : dialog de confirmation homonymes
  const [similarDialog, setSimilarDialog] = useState(null); // {similar: [...], pendingForm}
  // iter90gk : candidats BCE cousins pour rattachement (fiche sans BCE)
  const [bceCandidates, setBceCandidates] = useState([]);

  const load = useCallback(async () => {
    const { data } = await api.get('/suppliers', { params: search ? { search } : {} });
    setSuppliers(data);
  }, [search]);
  useEffect(() => { load(); }, [load]);

  const filtered = suppliers;
  const openCreate = () => { setEditing(null); setForm({ name:'', vat_number:'', bce_number:'', address:'', postal_code:'', city:'', country:'Belgique', phone:'', email:'', iban:'', bic:'', default_account:'', notes:'' }); setBceCandidates([]); setDialogOpen(true); };
  const openEdit = async (s) => {
    setEditing(s);
    setForm({ name:s.name, vat_number:s.vat_number||'', bce_number:s.bce_number||'', address:s.address||'', postal_code:s.postal_code||'', city:s.city||'', country:s.country||'Belgique', phone:s.phone||'', email:s.email||'', iban:s.iban||'', bic:s.bic||'', default_account:s.default_account||'', notes:s.notes||'' });
    setBceCandidates([]);
    setDialogOpen(true);
    // iter90gk : si la fiche n'a pas de BCE, on cherche les cousins ayant un
    // BCE renseigne pour proposer un rattachement (evite de garder un doublon).
    if (!s.bce_number) {
      try {
        const { data } = await api.get(`/suppliers/${s.id}/bce-candidates`);
        if (data.candidates?.length > 0) setBceCandidates(data.candidates);
      } catch { /* silent */ }
    }
  };

  // iter90gk : rattache la fiche courante a un fournisseur cousin ayant un BCE
  // via une fusion. Merge = data preserves + delete slave.
  const handleAttachToCandidate = async (candidate) => {
    if (!editing) return;
    const ok = window.confirm(
      `Rattacher la fiche courante "${editing.name}" au fournisseur cousin "${candidate.name}" (BCE ${candidate.bce_number}) ?\n\n` +
      `Cette action va :\n` +
      `- Reassigner toutes les factures et ecritures de la fiche courante vers "${candidate.name}"\n` +
      `- Fusionner les copropriete_ids et tier_accounts\n` +
      `- Supprimer la fiche courante "${editing.name}"\n\n` +
      `Cette action est irreversible.`
    );
    if (!ok) return;
    try {
      // Utilise le merge endpoint existant : garde le candidate (avec BCE), supprime la fiche courante
      await api.post('/suppliers/merge', {
        keep_id: candidate.id,
        remove_ids: [editing.id],
      });
      toast.success(`Rattachement effectue vers ${candidate.name}`);
      setDialogOpen(false);
      setBceCandidates([]);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur rattachement');
    }
  };

  const performCreate = async (formToUse, forceDespiteSimilar = false) => {
    try {
      await api.post('/suppliers', { ...formToUse, force_create_despite_similar: forceDespiteSimilar });
      toast.success('Fournisseur cree');
      setDialogOpen(false);
      setSimilarDialog(null);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleSave = async () => {
    // iter90ik : BCE OBLIGATOIRE (regle utilisateur). Bloque la creation
    // OU la modification si le BCE (ou TVA equivalent) est absent.
    const bce = (form.bce_number || '').trim().replace(/[.\s]/g, '');
    const vat = (form.vat_number || '').trim().replace(/[.\s]/g, '');
    if (!bce && !vat) {
      toast.error(
        "Le numero BCE (ou TVA equivalent) est obligatoire. Verifiez sur https://kbopub.economie.fgov.be/",
        { duration: 8000 },
      );
      return;
    }
    if (bce && !/^[A-Z]{2}[0-9]{8,12}$/i.test(bce)) {
      toast.error(`Format BCE invalide : "${form.bce_number}". Attendu : BE0123456789 (2 lettres pays + 8-12 chiffres).`);
      return;
    }
    try {
      if (editing) {
        await api.put(`/suppliers/${editing.id}`, form);
        toast.success('Fournisseur modifie');
        setDialogOpen(false);
        load();
        return;
      }
      // Creation : pre-verifie les homonymes pour proposer un dialog explicite
      const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
      const { data } = await api.post('/suppliers/check-duplicate', {
        name: form.name,
        vat_number: form.vat_number || '',
        bce_number: form.bce_number || '',
        iban: form.iban || '',
        copropriete_id: copro,
      });
      if (data.exact) {
        // Doublon strict -> on bloque (le POST renverrait 409 de toute facon)
        const e = data.exact;
        const label = ({ bce_number: 'numero BCE', vat_number: 'numero TVA', iban: 'IBAN', name: 'nom' })[e.field] || e.field;
        toast.error(`Doublon strict : un fournisseur avec le meme ${label} existe deja (${e.supplier?.name || ''}).`);
        return;
      }
      if (data.similar && data.similar.length > 0) {
        // Homonymes proches -> dialog de confirmation
        setSimilarDialog({ similar: data.similar, pendingForm: form });
        return;
      }
      // Aucune similitude -> creation directe
      await performCreate(form, false);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const handleDelete = async (id) => { if (!window.confirm('Supprimer ?')) return; await api.delete(`/suppliers/${id}`); toast.success('Supprime'); load(); };

  return (
    <div data-testid="suppliers-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Truck size={24} className="inline mr-2" />Fournisseurs</h1><p className="page-subtitle">Gestion des fournisseurs et prestataires</p></div>
        <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-supplier-btn"><Plus size={16} className="mr-2" /> Nouveau</Button>
      </div>
      <div className="mb-4 flex items-center gap-3 flex-wrap">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="suppliers-search" />
        </div>
        <span className="text-[11px] text-slate-500 italic hidden md:inline">
          Vue globale : tous les fournisseurs de vos ACPs
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => window.location.assign('/admin/duplicates?tab=suppliers')}
          className="text-xs border-orange-300 text-orange-700 hover:bg-orange-50"
          data-testid="suppliers-detect-duplicates-btn"
        >
          <AlertTriangle size={13} className="mr-1.5" /> Detecter les doublons
        </Button>
      </div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>N TVA</TableHead><TableHead>Ville</TableHead>
            <TableHead>Compte tier</TableHead>
            <TableHead>IBAN</TableHead><TableHead>Email</TableHead><TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucun fournisseur</TableCell></TableRow>
            ) : filtered.map(s => (
              <TableRow key={s.id} className="hover:bg-slate-50/50">
                <TableCell className="font-medium" data-testid={`supplier-name-${s.id}`}>{s.name}</TableCell>
                <TableCell className="font-mono text-sm">{s.vat_number || '-'}</TableCell>
                <TableCell>{s.city}</TableCell>
                {/* iter90iw : nouvelle colonne "Compte tier" (44000XXX) */}
                <TableCell className="font-mono text-sm" data-testid={`supplier-tier-account-${s.id}`}>
                  {s.tier_account_number || '-'}
                </TableCell>
                <TableCell className="font-mono text-sm">{s.iban || '-'}</TableCell>
                <TableCell>{s.email}</TableCell>
                <TableCell><div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(s)}><Pencil size={14} /></Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(s.id)} className="text-red-500"><Trash2 size={14} /></Button>
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl" data-testid="supplier-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier fournisseur' : 'Nouveau fournisseur'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            {/* iter90gk : Bloc "Rattacher a un fournisseur existant avec BCE" */}
            {editing && bceCandidates.length > 0 && !form.bce_number && (
              <div className="p-3 bg-amber-50 border border-amber-300 rounded" data-testid="bce-candidates-box">
                <div className="flex items-start gap-2 mb-2">
                  <AlertTriangle size={16} className="text-amber-600 mt-0.5" />
                  <div>
                    <div className="font-semibold text-amber-900 text-sm">Fiche sans BCE - rattachement possible</div>
                    <div className="text-xs text-amber-800 mt-1">
                      Cette fiche n&apos;a pas de BCE. {bceCandidates.length} fournisseur(s) similaire(s) avec BCE detecte(s).
                      Rattachez-les pour eviter les doublons (fusion automatique des factures et ecritures).
                    </div>
                  </div>
                </div>
                <div className="space-y-1 mt-2">
                  {bceCandidates.slice(0, 5).map((c) => (
                    <div key={c.id} className="flex items-center justify-between bg-white rounded px-2 py-1 text-xs">
                      <div>
                        <span className="font-semibold">{c.name}</span>
                        <span className="text-slate-500 ml-2">BCE {c.bce_number}</span>
                        <span className="text-slate-400 ml-2">
                          - {c.usage_count.invoices} facture(s), {c.usage_count.journal_entries} ecriture(s)
                        </span>
                        <span className="text-slate-400 ml-2 italic">(match par {c.matched_by})</span>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-xs py-0 h-6"
                        onClick={() => handleAttachToCandidate(c)}
                        data-testid={`bce-attach-${c.id}`}
                      >
                        Rattacher
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="supplier-name" /></div>
              <div><label className="form-label">N BCE *</label><Input value={form.bce_number} onChange={e => setForm({...form, bce_number: e.target.value})} placeholder="BE0123456789" data-testid="supplier-bce" required /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">N TVA (si different du BCE)</label><Input value={form.vat_number} onChange={e => setForm({...form, vat_number: e.target.value})} placeholder="BE0123.456.789" data-testid="supplier-vat" /></div>
              <div className="text-xs text-slate-500 pt-6">
                <a href="https://kbopub.economie.fgov.be/" target="_blank" rel="noreferrer" className="underline text-blue-600">Verifier le BCE en ligne</a>
              </div>
            </div>
            <div><label className="form-label">Adresse</label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} /></div>
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Code postal</label><Input value={form.postal_code} onChange={e => setForm({...form, postal_code: e.target.value})} /></div>
              <div><label className="form-label">Ville</label><Input value={form.city} onChange={e => setForm({...form, city: e.target.value})} /></div>
              <div><label className="form-label">Pays</label><Input value={form.country} onChange={e => setForm({...form, country: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">IBAN</label><Input value={form.iban} onChange={e => setForm({...form, iban: e.target.value})} placeholder="BE00 0000 0000 0000" /></div>
              <div><label className="form-label">BIC</label><Input value={form.bic} onChange={e => setForm({...form, bic: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Telephone</label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} /></div>
              <div><label className="form-label">Email</label><Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button
                onClick={handleSave}
                disabled={!form.name?.trim() || (!form.bce_number?.trim() && !form.vat_number?.trim())}
                className="bg-[#022D52] hover:bg-[#1D4ED8]"
                data-testid="supplier-save-btn"
                title={(!form.bce_number?.trim() && !form.vat_number?.trim()) ? "BCE ou TVA obligatoire" : ""}
              >{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter85g : Dialog de confirmation homonymes */}
      <Dialog open={!!similarDialog} onOpenChange={(o) => !o && setSimilarDialog(null)}>
        <DialogContent className="max-w-2xl" data-testid="similar-suppliers-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="flex items-center gap-2 text-amber-700">
              <AlertTriangle size={20} /> Homonymes potentiels detectes
            </DialogTitle>
          </DialogHeader>
          {similarDialog && (
            <div className="space-y-4 mt-2">
              <p className="text-sm text-slate-700">
                Vous etes sur le point de creer le fournisseur <b>&quot;{similarDialog.pendingForm.name}&quot;</b>.
                Les fournisseurs suivants existent deja avec un nom similaire :
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded p-3 space-y-2 max-h-72 overflow-auto">
                {similarDialog.similar.map((m, i) => (
                  <div key={i} className="flex items-center justify-between bg-white border border-amber-100 rounded p-2" data-testid={`similar-supplier-${i}`}>
                    <div className="flex-1">
                      <div className="font-medium text-sm">{m.supplier.name}</div>
                      <div className="text-[11px] text-slate-500 space-x-3">
                        {m.supplier.vat_number && <span>TVA: {m.supplier.vat_number}</span>}
                        {m.supplier.city && <span>{m.supplier.city}</span>}
                        {m.supplier.iban && <span className="font-mono">{m.supplier.iban}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-amber-700">{Math.round(m.score * 100)}% similarite</span>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-[#01213e] border-blue-200 h-7 text-[11px]"
                        onClick={() => {
                          // Pre-rempli le formulaire avec le supplier existant (mode edit) puis annule la creation
                          openEdit(m.supplier);
                          setSimilarDialog(null);
                        }}
                        data-testid={`use-existing-supplier-${i}`}
                      >
                        Utiliser celui-ci
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-slate-500 italic">
                Si aucun ne correspond, vous pouvez creer un nouveau fournisseur quand meme.
              </p>
              <div className="flex gap-3 justify-end">
                <Button variant="outline" onClick={() => setSimilarDialog(null)} data-testid="similar-cancel-btn">
                  Annuler
                </Button>
                <Button
                  onClick={() => performCreate(similarDialog.pendingForm, true)}
                  className="bg-amber-600 hover:bg-amber-700 text-white"
                  data-testid="similar-force-create-btn"
                >
                  Creer quand meme
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
