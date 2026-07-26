import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { Plus, Trash2, Droplets, Flame, Zap, Activity, Wrench, Flame as FlameGas } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const METER_TYPES = [
  { value: 'water', label: 'Eau', icon: Droplets, color: '#0284C7' },
  { value: 'heating', label: 'Chauffage', icon: Flame, color: '#FF6B00' },
  { value: 'electricity', label: 'Electricite', icon: Zap, color: '#00A650' },
  { value: 'gas', label: 'Gaz', icon: FlameGas, color: '#D97706' },
  { value: 'boiler_maintenance', label: 'Entretien chaudiere', icon: Wrench, color: '#7C3AED' },
];

export default function MetersPage() {
  const [meters, setMeters] = useState([]);
  const [lots, setLots] = useState([]);
  const [selectedMeter, setSelectedMeter] = useState(null);
  const [readings, setReadings] = useState([]);
  const [meterDialog, setMeterDialog] = useState(false);
  const [readingDialog, setReadingDialog] = useState(false);
  const [meterForm, setMeterForm] = useState({ name: '', meter_type: 'water', unit: '', lot_id: '', serial_number: '' });
  const [readingForm, setReadingForm] = useState({ date: '', value: 0 });

  const load = useCallback(async () => {
    const [m, l] = await Promise.all([api.get('/meters'), api.get('/lots')]);
    setMeters(m.data); setLots(l.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const loadReadings = async (meter) => {
    setSelectedMeter(meter);
    const { data } = await api.get(`/meters/${meter.id}/readings`);
    setReadings(data);
  };

  const getLotNumber = (lotId) => {
    if (!lotId) return 'Commun';
    return lots.find(l => l.id === lotId)?.number || '-';
  };

  const openCreateMeter = () => { setMeterForm({ name: '', meter_type: 'water', unit: '', lot_id: '', serial_number: '' }); setMeterDialog(true); };

  const saveMeter = async () => {
    try {
      const payload = { ...meterForm };
      // Un compteur "Commun" est stocke sans lot_id
      if (payload.lot_id === 'none') payload.lot_id = '';
      await api.post('/meters', payload);
      toast.success('Compteur cree'); setMeterDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteMeter = async (id) => {
    if (!window.confirm('Supprimer ce compteur ?')) return;
    await api.delete(`/meters/${id}`); toast.success('Compteur supprime'); load();
    if (selectedMeter?.id === id) { setSelectedMeter(null); setReadings([]); }
  };

  const openAddReading = () => { setReadingForm({ date: new Date().toISOString().split('T')[0], value: 0 }); setReadingDialog(true); };

  const saveReading = async () => {
    try {
      await api.post(`/meters/${selectedMeter.id}/readings`, { ...readingForm, value: Number(readingForm.value) });
      toast.success('Releve enregistre'); setReadingDialog(false); loadReadings(selectedMeter);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteReading = async (readingId) => {
    await api.delete(`/meters/${selectedMeter.id}/readings/${readingId}`);
    toast.success('Releve supprime'); loadReadings(selectedMeter);
  };

  const getTypeInfo = (type) => METER_TYPES.find(t => t.value === type) || METER_TYPES[0];

  // iter90g5 : Releve multi-lots (1 releve -> N lots)
  const [batchDialog, setBatchDialog] = useState(false);
  const [batchDate, setBatchDate] = useState(new Date().toISOString().slice(0, 10));
  const [batchType, setBatchType] = useState('water');
  const [batchRows, setBatchRows] = useState({}); // {lot_id: value}
  const [batchLotIds, setBatchLotIds] = useState(new Set());
  const [batchSubmitting, setBatchSubmitting] = useState(false);

  const openBatch = () => {
    setBatchDate(new Date().toISOString().slice(0, 10));
    setBatchType('water');
    setBatchLotIds(new Set());
    setBatchRows({});
    setBatchDialog(true);
  };

  const submitBatch = async () => {
    const entries = Array.from(batchLotIds)
      .map(lot_id => ({ lot_id, value: Number(batchRows[lot_id] || 0) }))
      .filter(e => !Number.isNaN(e.value));
    if (entries.length === 0) {
      toast.error('Selectionnez au moins un lot');
      return;
    }
    const coproId = lots[0]?.copropriete_id;
    if (!coproId) { toast.error('ACP inconnue'); return; }
    setBatchSubmitting(true);
    try {
      const r = await api.post('/meters/batch-readings', {
        date: batchDate,
        meter_type: batchType,
        copropriete_id: coproId,
        entries,
      });
      toast.success(`${r.data.count} releve(s) cree(s)`);
      setBatchDialog(false);
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur releve multi-lots');
    } finally {
      setBatchSubmitting(false);
    }
  };

  return (
    <div data-testid="meters-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Compteurs</h1><p className="page-subtitle">Gestion des compteurs et releves</p></div>
        <div className="flex gap-2">
          <Button onClick={openBatch} variant="outline" data-testid="batch-reading-btn"><Plus size={16} className="mr-2" /> Releve multi-lots</Button>
          <Button onClick={openCreateMeter} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-meter-btn"><Plus size={16} className="mr-2" /> Nouveau compteur</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Meters list */}
        <div className="lg:col-span-1 space-y-3">
          {meters.length === 0 ? (
            <p className="text-sm text-slate-400 text-center py-8">Aucun compteur</p>
          ) : meters.map(m => {
            const info = getTypeInfo(m.meter_type);
            const Icon = info.icon;
            return (
              <Card
                key={m.id}
                className={`cursor-pointer transition-all border ${selectedMeter?.id === m.id ? 'border-[#022D52] shadow-md' : 'border-slate-200 hover:border-slate-300'}`}
                onClick={() => loadReadings(m)}
                data-testid={`meter-card-${m.id}`}
              >
                <CardContent className="p-4 flex items-center gap-3">
                  <div className="w-10 h-10 rounded-md flex items-center justify-center" style={{backgroundColor: info.color + '15', color: info.color}}>
                    <Icon size={20} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm text-slate-900 truncate">{m.name}</div>
                    <div className="text-xs text-slate-500">Lot {getLotNumber(m.lot_id)} - {m.unit}</div>
                  </div>
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); deleteMeter(m.id); }} className="text-red-400 hover:text-red-600">
                    <Trash2 size={14} />
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Readings */}
        <div className="lg:col-span-2">
          {selectedMeter ? (
            <Card className="border-slate-200">
              <CardHeader className="pb-3 flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-lg" style={{fontFamily:'Chivo,sans-serif'}}>
                    <Activity size={16} className="inline mr-2" />Releves - {selectedMeter.name}
                  </CardTitle>
                  <p className="text-xs text-slate-500 mt-1">Unite: {selectedMeter.unit}</p>
                </div>
                <Button onClick={openAddReading} className="bg-[#022D52] hover:bg-[#1D4ED8]" size="sm" data-testid="add-reading-btn">
                  <Plus size={14} className="mr-1" /> Releve
                </Button>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Date</TableHead><TableHead className="text-right">Index</TableHead>
                    <TableHead className="text-right">Consommation</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {readings.length === 0 ? (
                      <TableRow><TableCell colSpan={4} className="text-center py-8 text-slate-400">Aucun releve</TableCell></TableRow>
                    ) : readings.map(r => (
                      <TableRow key={r.id}>
                        <TableCell className="font-mono text-sm">{fmtDate(r.date)}</TableCell>
                        <TableCell className="text-right font-mono">{r.value}</TableCell>
                        <TableCell className="text-right">
                          {r.consumption > 0 && <Badge variant="outline" className="font-mono">{r.consumption} {selectedMeter.unit}</Badge>}
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" onClick={() => deleteReading(r.id)} className="text-red-400"><Trash2 size={12} /></Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">
              Selectionnez un compteur pour voir les releves
            </div>
          )}
        </div>
      </div>

      {/* Create Meter Dialog */}
      <Dialog open={meterDialog} onOpenChange={setMeterDialog}>
        <DialogContent data-testid="meter-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouveau compteur</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={meterForm.name} onChange={e => setMeterForm({...meterForm, name: e.target.value})} data-testid="meter-name" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Type</label>
                <Select value={meterForm.meter_type} onValueChange={v => setMeterForm({...meterForm, meter_type: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{METER_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div><label className="form-label">N de serie</label><Input value={meterForm.serial_number} onChange={e => setMeterForm({...meterForm, serial_number: e.target.value})} /></div>
            </div>
            <div>
              <label className="form-label">Lot</label>
              <Select value={meterForm.lot_id} onValueChange={v => setMeterForm({...meterForm, lot_id: v})}>
                <SelectTrigger><SelectValue placeholder="Selectionner un lot" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Commun</SelectItem>
                  {lots.map(l => <SelectItem key={l.id} value={l.id}>Lot {l.number}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setMeterDialog(false)}>Annuler</Button>
              <Button onClick={saveMeter} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="meter-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add Reading Dialog */}
      <Dialog open={readingDialog} onOpenChange={setReadingDialog}>
        <DialogContent data-testid="reading-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouveau releve</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Date *</label><Input type="date" value={readingForm.date} onChange={e => setReadingForm({...readingForm, date: e.target.value})} /></div>
            <div><label className="form-label">Index ({selectedMeter?.unit}) *</label><Input type="number" step="0.01" value={readingForm.value} onChange={e => setReadingForm({...readingForm, value: e.target.value})} data-testid="reading-value" /></div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setReadingDialog(false)}>Annuler</Button>
              <Button onClick={saveReading} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="reading-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter90g5 : Dialog releve multi-lots */}
      <Dialog open={batchDialog} onOpenChange={setBatchDialog}>
        <DialogContent className="max-w-2xl" data-testid="batch-reading-dialog">
          <DialogHeader><DialogTitle>Releve multi-lots</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs uppercase tracking-wider text-slate-500 font-semibold block mb-1">Date du releve</label>
                <Input type="date" value={batchDate} onChange={e => setBatchDate(e.target.value)} className="h-9" data-testid="batch-date" />
              </div>
              <div>
                <label className="text-xs uppercase tracking-wider text-slate-500 font-semibold block mb-1">Type de compteur</label>
                <select value={batchType} onChange={e => setBatchType(e.target.value)} className="h-9 w-full border border-slate-200 rounded px-2 text-sm" data-testid="batch-type">
                  <option value="water">Eau (m3)</option>
                  <option value="heating">Chauffage (kWh)</option>
                  <option value="electricity">Electricite (kWh)</option>
                  <option value="gas">Gaz (m3)</option>
                  <option value="boiler_maintenance">Entretien chaudiere (part)</option>
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-slate-500 font-semibold block mb-1">Lots concernes ({batchLotIds.size} selectionnes)</label>
              <div className="border border-slate-200 rounded max-h-80 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 sticky top-0">
                    <tr className="text-xs text-slate-600 uppercase">
                      <th className="p-2 text-left w-10">
                        <input type="checkbox" onChange={(e) => {
                          if (e.target.checked) setBatchLotIds(new Set(lots.map(l => l.id)));
                          else setBatchLotIds(new Set());
                        }} data-testid="batch-toggle-all" />
                      </th>
                      <th className="p-2 text-left">Lot</th>
                      <th className="p-2 text-left">Description</th>
                      <th className="p-2 text-right">Valeur / Index</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lots.map(l => {
                      const checked = batchLotIds.has(l.id);
                      return (
                        <tr key={l.id} className={checked ? 'bg-blue-50/50' : ''} data-testid={`batch-lot-row-${l.id}`}>
                          <td className="p-2">
                            <input type="checkbox" checked={checked} onChange={(e) => {
                              const s = new Set(batchLotIds);
                              if (e.target.checked) s.add(l.id); else s.delete(l.id);
                              setBatchLotIds(s);
                            }} data-testid={`batch-lot-check-${l.id}`} />
                          </td>
                          <td className="p-2 font-mono text-xs">{l.number}</td>
                          <td className="p-2 text-xs text-slate-500">{l.description || '-'}</td>
                          <td className="p-2 text-right">
                            <Input type="number" step="0.01" disabled={!checked} value={batchRows[l.id] || ''} onChange={e => setBatchRows({ ...batchRows, [l.id]: e.target.value })} className="h-8 text-right font-mono text-xs w-32 ml-auto" data-testid={`batch-lot-value-${l.id}`} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setBatchDialog(false)} data-testid="batch-cancel-btn">Annuler</Button>
              <Button onClick={submitBatch} disabled={batchSubmitting || batchLotIds.size === 0} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="batch-save-btn">
                {batchSubmitting ? 'Envoi...' : `Enregistrer ${batchLotIds.size} releve(s)`}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
