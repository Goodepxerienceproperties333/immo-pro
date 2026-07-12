/**
 * Generic CSV import dialog with auto column mapping.
 *
 * - Parses CSV in-browser (no backend call).
 * - Auto-detects separator (`;`, `,`, tab) and tries to map common Optipro/Sogis
 *   column names to target fields.
 * - Lets the user adjust the mapping then calls `onImport(rows)` where each row
 *   is a clean object with target field names as keys.
 */
import { useState, useRef } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Upload, FileText, AlertTriangle, CheckCircle2 } from 'lucide-react';

function detectSep(firstLine) {
  const cands = { ';': firstLine.split(';').length, ',': firstLine.split(',').length, '\t': firstLine.split('\t').length };
  return Object.keys(cands).reduce((a, b) => (cands[a] > cands[b] ? a : b));
}

function parseCsvText(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return { headers: [], rows: [], sep: ',' };
  const sep = detectSep(lines[0]);
  const split = (line) => line.split(sep).map(c => c.trim().replace(/^["']|["']$/g, ''));
  const headers = split(lines[0]);
  const rows = lines.slice(1).map(split);
  return { headers, rows, sep };
}

function normalize(s) {
  if (!s) return '';
  return s.toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]/g, '');
}

function autoMap(headers, fields) {
  // For each target field, find the header whose normalized form contains one of the synonyms
  const mapping = {};
  headers.forEach((h, i) => {
    const nh = normalize(h);
    fields.forEach(f => {
      if (mapping[f.key] !== undefined) return;
      const syns = [normalize(f.key), normalize(f.label)].concat((f.synonyms || []).map(normalize));
      if (syns.some(s => s && (nh === s || nh.includes(s) || s.includes(nh)))) {
        mapping[f.key] = i.toString();
      }
    });
  });
  return mapping;
}

export default function BulkCsvImportDialog({ open, onClose, title, targetFields, onImport, encoding = 'utf-8' }) {
  const inputRef = useRef(null);
  const [parsed, setParsed] = useState(null);
  const [mapping, setMapping] = useState({});
  const [importing, setImporting] = useState(false);

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const p = parseCsvText(text);
    setParsed(p);
    setMapping(autoMap(p.headers, targetFields));
  };

  const handleImport = () => {
    if (!parsed) return;
    setImporting(true);
    const records = parsed.rows.map(row => {
      const o = {};
      targetFields.forEach(f => {
        const idx = mapping[f.key];
        if (idx !== undefined && idx !== '') {
          o[f.key] = (row[parseInt(idx)] || '').trim();
        }
      });
      return o;
    }).filter(r => {
      // Drop rows where all required fields are empty
      const req = targetFields.filter(f => f.required);
      return req.every(f => r[f.key] && r[f.key].trim());
    });
    onImport(records);
    setImporting(false);
    reset();
    onClose();
  };

  const reset = () => { setParsed(null); setMapping({}); };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { reset(); onClose(); } }}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" data-testid="bulk-csv-dialog">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2"><Upload size={16} className="text-[#022D52]" /> {title}</DialogTitle>
        </DialogHeader>
        {!parsed && (
          <div className="border-2 border-dashed border-slate-300 rounded-md p-6 text-center">
            <FileText size={28} className="mx-auto text-slate-400 mb-2" />
            <p className="text-xs text-slate-600 mb-3">Chargez un fichier CSV exporte depuis Optipro / Sogis (ou Excel)</p>
            <input ref={inputRef} type="file" accept=".csv,.txt" className="hidden" onChange={handleFile} data-testid="bulk-csv-input" />
            <Button onClick={() => inputRef.current?.click()} className="bg-[#022D52] hover:bg-[#1D4ED8]"><Upload size={13} className="mr-1" /> Choisir le fichier</Button>
          </div>
        )}
        {parsed && (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Separateur</div><div className="font-mono font-semibold">&laquo;{parsed.sep === '\t' ? 'TAB' : parsed.sep}&raquo;</div></div>
              <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Colonnes</div><div className="font-mono font-semibold">{parsed.headers.length}</div></div>
              <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Lignes</div><div className="font-mono font-semibold">{parsed.rows.length}</div></div>
            </div>

            <div className="bg-blue-50 border border-blue-200 rounded p-2 text-xs text-blue-900">
              <strong>Mapping des colonnes :</strong> les colonnes du CSV ont ete auto-detectees. Verifiez/ajustez ci-dessous.
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-60 overflow-y-auto">
              {targetFields.map(f => (
                <div key={f.key} className="flex items-center gap-2">
                  <label className="text-xs w-32 flex-shrink-0 text-slate-700">{f.label}{f.required && ' *'}</label>
                  <Select
                    value={mapping[f.key] ?? '__none__'}
                    onValueChange={(v) => setMapping({ ...mapping, [f.key]: v === '__none__' ? '' : v })}
                  >
                    <SelectTrigger className="h-8 text-xs" data-testid={`bulk-map-${f.key}`}>
                      <SelectValue placeholder="-- Ignorer --" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">-- Ignorer --</SelectItem>
                      {parsed.headers.map((h, i) => (<SelectItem key={i} value={i.toString()}>{h || `Col ${i+1}`}</SelectItem>))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>

            <div className="border border-slate-200 rounded overflow-x-auto text-xs">
              <table className="w-full">
                <thead className="bg-slate-50"><tr>{parsed.headers.map((h, i) => <th key={i} className="px-2 py-1 text-left border-r border-slate-200 whitespace-nowrap font-medium">{h || `Col ${i+1}`}</th>)}</tr></thead>
                <tbody>
                  {parsed.rows.slice(0, 6).map((row, ri) => (
                    <tr key={ri} className="border-t border-slate-100">
                      {parsed.headers.map((_, ci) => <td key={ci} className="px-2 py-1 border-r border-slate-100 truncate max-w-[180px]" title={row[ci]}>{row[ci]}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              {parsed.rows.length > 6 && <div className="text-[10px] text-slate-400 px-2 py-1 bg-slate-50">... et {parsed.rows.length - 6} autres lignes</div>}
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={reset}>Choisir un autre fichier</Button>
              <Button onClick={handleImport} disabled={importing} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="bulk-csv-confirm">
                <CheckCircle2 size={13} className="mr-1" /> Importer {parsed.rows.length} ligne(s)
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
