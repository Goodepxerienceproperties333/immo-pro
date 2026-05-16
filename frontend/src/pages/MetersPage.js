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
import { Plus, Trash2, Droplets, Flame, Zap, Activity } from 'lucide-react';

const METER_TYPES = [
  { value: 'water', label: 'Eau', icon: Droplets, color: '#0284C7' },
  { value: 'heating', label: 'Chauffage', icon: Flame, color: '#FF6B00' },
  { value: 'electricity', label: 'Electricite', icon: Zap, color: '#00A650' },
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

  const getLotNumber = (lotId) => lots.find(l => l.id === lotId)?.number || '-';

  const openCreateMeter = () => { setMeterForm({ name: '', meter_type: 'water', unit: '', lot_id: '', serial_number: '' }); setMeterDialog(true); };

  const saveMeter = async () => {
    try {
      await api.post('/meters', meterForm);
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

  return (
    <div data-testid="meters-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Compteurs</h1><p className="page-subtitle">Gestion des compteurs et releves</p></div>
        <Button onClick={openCreateMeter} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-meter-btn"><Plus size={16} className="mr-2" /> Nouveau compteur</Button>
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
                className={`cursor-pointer transition-all border ${selectedMeter?.id === m.id ? 'border-[#0055FF] shadow-md' : 'border-slate-200 hover:border-slate-300'}`}
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
                <Button onClick={openAddReading} className="bg-[#0055FF] hover:bg-[#0040CC]" size="sm" data-testid="add-reading-btn">
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
                        <TableCell className="font-mono text-sm">{r.date}</TableCell>
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
                  <SelectItem value="">Commun</SelectItem>
                  {lots.map(l => <SelectItem key={l.id} value={l.id}>Lot {l.number}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setMeterDialog(false)}>Annuler</Button>
              <Button onClick={saveMeter} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="meter-save-btn">Creer</Button>
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
              <Button onClick={saveReading} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="reading-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
