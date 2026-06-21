import { useState, useRef } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { FileText, Upload, AlertCircle, CheckCircle2 } from 'lucide-react';

/**
 * Generic PDF import dialog used by the ACP creation wizard.
 *
 * Props:
 *  - open, onClose
 *  - title : dialog title
 *  - kind : 'owners' | 'lots' (passed to the backend parser)
 *  - columns : [{ key, label, monospace?, width? }] - columns to display in preview table
 *  - dataKey : the key in the API response holding the array (e.g. 'owners', 'lots')
 *  - onImport(selectedRows) : called when user clicks Import (rows are the user-selected items)
 *  - hint : optional explanatory string shown in the upload zone
 */
export default function PdfImportDialog({ open, onClose, title, kind, columns, dataKey, onImport, hint }) {
  const fileRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [fileName, setFileName] = useState('');

  const reset = () => {
    setRows([]);
    setSelected(new Set());
    setFileName('');
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileName(f.name);
    setLoading(true);
    setRows([]);
    setSelected(new Set());
    try {
      const form = new FormData();
      form.append('file', f);
      form.append('kind', kind);
      const { data } = await api.post('/import-wizard/parse-pdf', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const parsed = data?.[dataKey] || [];
      setRows(parsed);
      // Pre-select all by default
      setSelected(new Set(parsed.map((_r, i) => i)));
      if (parsed.length === 0) {
        toast.error('Aucune ligne detectee dans le PDF. Verifiez que c\'est bien un export Optipro/Sogis.');
      } else {
        toast.success(`${parsed.length} ligne(s) detectee(s)`);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de l\'analyse du PDF');
    } finally {
      setLoading(false);
    }
  };

  const toggle = (i) => {
    const next = new Set(selected);
    if (next.has(i)) next.delete(i); else next.add(i);
    setSelected(next);
  };
  const toggleAll = () => {
    if (selected.size === rows.length) setSelected(new Set());
    else setSelected(new Set(rows.map((_r, i) => i)));
  };

  const handleImport = () => {
    const picked = rows.filter((_r, i) => selected.has(i));
    if (picked.length === 0) {
      toast.error('Selectionnez au moins une ligne');
      return;
    }
    onImport(picked);
    reset();
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-5xl max-h-[90vh] flex flex-col" data-testid="pdf-import-dialog">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Chivo,sans-serif' }} className="flex items-center gap-2">
            <FileText size={18} className="text-emerald-600" />
            {title}
          </DialogTitle>
        </DialogHeader>

        {rows.length === 0 ? (
          <div className="border-2 border-dashed border-slate-200 rounded-lg p-8 text-center">
            <Upload size={32} className="mx-auto text-slate-300 mb-2" />
            <p className="text-sm text-slate-600 mb-3">
              Telechargez un PDF d&apos;export Optipro / Sogis
              {hint ? <span className="block text-xs text-slate-400 mt-1">{hint}</span> : null}
            </p>
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              onChange={handleFile}
              className="hidden"
              data-testid="pdf-file-input"
            />
            <Button
              onClick={() => fileRef.current?.click()}
              disabled={loading}
              className="bg-emerald-600 hover:bg-emerald-700"
              data-testid="pdf-choose-file-btn"
            >
              {loading ? 'Analyse en cours...' : 'Choisir un fichier PDF'}
            </Button>
            {fileName && <div className="mt-2 text-xs text-slate-500 flex items-center justify-center gap-1"><FileText size={10} />{fileName}</div>}
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between text-xs text-slate-600 px-1">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={14} className="text-emerald-600" />
                <span>
                  <strong>{rows.length}</strong> ligne(s) detectee(s) dans <span className="font-mono">{fileName}</span> -
                  <button onClick={toggleAll} className="ml-1 text-emerald-700 underline" data-testid="pdf-toggle-all">
                    {selected.size === rows.length ? 'Tout deselectionner' : 'Tout selectionner'}
                  </button>
                </span>
              </div>
              <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">
                {selected.size} / {rows.length} selectionnees
              </Badge>
            </div>

            <div className="border rounded-md overflow-auto flex-1 min-h-[200px]">
              <Table>
                <TableHeader className="sticky top-0 bg-white z-10">
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    {columns.map((col) => (
                      <TableHead key={col.key} style={col.width ? { width: col.width } : {}}>{col.label}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => (
                    <TableRow
                      key={i}
                      className={selected.has(i) ? 'bg-emerald-50/40 hover:bg-emerald-50/70' : 'hover:bg-slate-50/50'}
                      data-testid={`pdf-row-${i}`}
                    >
                      <TableCell>
                        <Checkbox
                          checked={selected.has(i)}
                          onCheckedChange={() => toggle(i)}
                          data-testid={`pdf-row-check-${i}`}
                        />
                      </TableCell>
                      {columns.map((col) => (
                        <TableCell key={col.key} className={col.monospace ? 'font-mono text-xs' : 'text-sm'}>
                          {r[col.key] || <span className="text-slate-300">-</span>}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="text-[11px] text-amber-700 bg-amber-50/40 border border-amber-100 rounded p-2 flex gap-2">
              <AlertCircle size={14} className="shrink-0 text-amber-600 mt-0.5" />
              Verifiez les donnees parsees avant import. Les champs vides seront ignores ou laisses vides.
            </div>
          </>
        )}

        <div className="flex justify-between pt-2 border-t mt-2">
          <Button variant="outline" onClick={handleClose} data-testid="pdf-cancel-btn">Annuler</Button>
          {rows.length > 0 && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={reset} data-testid="pdf-reset-btn">Charger un autre fichier</Button>
              <Button
                onClick={handleImport}
                className="bg-emerald-600 hover:bg-emerald-700"
                disabled={selected.size === 0}
                data-testid="pdf-import-btn"
              >
                Importer {selected.size} ligne(s)
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
