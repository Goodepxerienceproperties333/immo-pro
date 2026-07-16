/*
 * iter90gk : Page admin "Quality Audit" - visualise le rapport
 * /api/admin/duplicates-audit avec actions rapides par ligne.
 *
 * Sections :
 *  - Fournisseurs : BCE dupliques, sans BCE (top 10), noms dupliques
 *  - Proprietaires : email/telephone dupliques
 *  - PCMN : tier accounts orphelins, comptes bancaires dupliques
 *  - Notes de Credit sans ecriture AC
 * Export CSV disponible.
 */
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle2, RefreshCw, Download, Users, Truck, FileWarning, Landmark, FileText } from 'lucide-react';
import api from '@/lib/api';

const SEV_COLOR = {
  ok: 'bg-emerald-50 border-emerald-200 text-emerald-900',
  warn: 'bg-yellow-50 border-yellow-200 text-yellow-900',
  err: 'bg-red-50 border-red-200 text-red-900',
};

function Section({ icon: Icon, title, count, children, severity = 'ok' }) {
  return (
    <Card className={`${SEV_COLOR[severity]} border`} data-testid={`section-${title.toLowerCase().replace(/\s+/g,'-')}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Icon size={16} />
            {title}
          </span>
          <Badge className={severity === 'ok' ? 'bg-emerald-600' : (severity === 'warn' ? 'bg-yellow-600' : 'bg-red-600')}>
            {count} {count === 1 ? 'entree' : 'entrees'}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default function AdminQualityAuditPage() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [copros, setCopros] = useState([]);
  const [coproFilter, setCoproFilter] = useState('all');

  const loadCopros = async () => {
    try {
      const { data } = await api.get('/coproprietes');
      setCopros(data || []);
    } catch { /* silent */ }
  };
  const load = async (opts = {}) => {
    setLoading(true);
    try {
      const params = coproFilter !== 'all' ? { copro_id: coproFilter } : {};
      if (opts.force) params.force_refresh = true;
      const { data } = await api.get('/admin/duplicates-audit', { params });
      setReport(data);
      if (opts.force) toast.success('Rapport regenere depuis la DB');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement rapport');
    } finally {
      setLoading(false);
    }
  };
  const downloadCsv = () => {
    const params = coproFilter !== 'all' ? `&copro_id=${coproFilter}` : '';
    window.open(`${process.env.REACT_APP_BACKEND_URL}/api/admin/duplicates-audit?format=csv${params}`, '_blank');
  };
  const goToSupplier = (id) => window.open(`/suppliers?highlight=${id}`, '_blank');
  const goToOwner = (id) => window.open(`/owners?highlight=${id}`, '_blank');

  useEffect(() => { loadCopros(); load(); /* eslint-disable-next-line */ }, []);

  const s = report?.summary || {};
  const healthy = report?.healthy === true;

  return (
    <div className="space-y-6" data-testid="admin-quality-audit-page">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold" style={{ fontFamily: 'Chivo,sans-serif' }}>Quality Audit</h1>
          <p className="text-sm text-slate-500 mt-1">
            Rapport global anti-doublons - regle stricte comptable
          </p>
          {report && (
            <p className="text-xs text-slate-400 mt-1">
              Genere le {new Date(report.generated_at).toLocaleString('fr-BE')} - Scope : {report.scope}
              {report._cache_hit && <span className="ml-2 text-emerald-600" data-testid="cache-hit-badge">(cache 5min)</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={coproFilter}
            onChange={(e) => setCoproFilter(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1"
            data-testid="quality-copro-filter"
          >
            <option value="all">Toutes les ACPs</option>
            {copros.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <Button size="sm" onClick={() => load()} disabled={loading} data-testid="quality-refresh">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Analyse...' : 'Actualiser'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => load({ force: true })} disabled={loading} data-testid="quality-force-refresh" title="Force regeneration depuis la DB (bypass cache 5min)">
            <RefreshCw size={14} className="mr-1" /> Force
          </Button>
          <Button size="sm" variant="outline" onClick={downloadCsv} data-testid="quality-download-csv">
            <Download size={14} className="mr-1" /> CSV
          </Button>
        </div>
      </div>

      {report && (
        <div className={`p-4 rounded border-2 ${healthy ? 'bg-emerald-50 border-emerald-400' : 'bg-yellow-50 border-yellow-400'}`}>
          <div className="flex items-center gap-3">
            {healthy ? (
              <CheckCircle2 size={24} className="text-emerald-600" />
            ) : (
              <AlertTriangle size={24} className="text-yellow-600" />
            )}
            <div>
              <div className="font-bold text-lg">
                {healthy ? 'Base saine - aucun doublon detecte' : 'Anomalies detectees - action requise'}
              </div>
              <div className="text-xs mt-1 flex flex-wrap gap-3">
                <span>BCE dup : <strong>{s.supplier_bce_duplicates || 0}</strong></span>
                <span>Suppliers sans BCE : <strong>{s.supplier_missing_bce || 0}</strong></span>
                <span>Owner email dup : <strong>{s.owner_email_dup_groups || 0}</strong></span>
                <span>Owner tel dup : <strong>{s.owner_phone_dup_groups || 0}</strong></span>
                <span>PCMN orphan tier : <strong>{s.pcmn_orphan_tier || 0}</strong></span>
                <span>Bank dup : <strong>{s.pcmn_dup_bank || 0}</strong></span>
                <span>NC sans ecriture : <strong>{s.credit_notes_missing_entry || 0}</strong></span>
                <span>Docs sans GridFS : <strong>{s.documents_without_gridfs || 0}</strong></span>
              </div>
            </div>
          </div>
        </div>
      )}

      {report && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* BCE duplicates */}
          <Section
            icon={Truck}
            title="Fournisseurs - BCE duplique"
            count={report.suppliers.bce_duplicates.length}
            severity={report.suppliers.bce_duplicates.length ? 'err' : 'ok'}
          >
            {report.suppliers.bce_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun BCE duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">BCE</TableHead>
                    <TableHead className="text-xs">Fiches</TableHead>
                    <TableHead className="w-16"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.suppliers.bce_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.bce}</TableCell>
                      <TableCell className="text-xs">
                        {g.suppliers.map(s => s.name).join(', ')}
                      </TableCell>
                      <TableCell>
                        <Button size="sm" variant="outline" onClick={() => goToSupplier(g.suppliers[0].id)} className="text-xs py-0 h-6">
                          Ouvrir
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          {/* Missing BCE (top 10 with usage) */}
          <Section
            icon={FileWarning}
            title="Fournisseurs sans BCE (utilises)"
            count={s.supplier_missing_bce || 0}
            severity={(s.supplier_missing_bce || 0) > 0 ? 'warn' : 'ok'}
          >
            {report.suppliers.missing_bce_examples.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun fournisseur utilise sans BCE</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">Top 10 les plus utilises :</p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Nom</TableHead>
                      <TableHead className="text-xs w-16">Factures</TableHead>
                      <TableHead className="w-16"></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.suppliers.missing_bce_examples.map((ex, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs">{ex.name}</TableCell>
                        <TableCell className="text-xs">{ex.invoices_using}</TableCell>
                        <TableCell>
                          <Button size="sm" variant="outline" onClick={() => goToSupplier(ex.id)} className="text-xs py-0 h-6">
                            Corriger
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Owner email/phone duplicates */}
          <Section
            icon={Users}
            title="Owners - Email duplique"
            count={report.owners.email_duplicates.length}
            severity={report.owners.email_duplicates.length ? 'err' : 'ok'}
          >
            {report.owners.email_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun email duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Email</TableHead>
                    <TableHead className="text-xs">Owners</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.owners.email_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.email}</TableCell>
                      <TableCell className="text-xs">
                        {g.owners.map((o, j) => (
                          <button
                            key={j}
                            className="underline text-blue-600 hover:text-blue-800 mr-2"
                            onClick={() => goToOwner(o.id)}
                          >
                            {o.name || o.id.slice(0,8)}
                          </button>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          <Section
            icon={Users}
            title="Owners - Telephone duplique"
            count={report.owners.phone_duplicates.length}
            severity={report.owners.phone_duplicates.length ? 'err' : 'ok'}
          >
            {report.owners.phone_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun telephone duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Telephone</TableHead>
                    <TableHead className="text-xs">Owners</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.owners.phone_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.phone}</TableCell>
                      <TableCell className="text-xs">
                        {g.owners.map((o, j) => (
                          <button key={j} className="underline text-blue-600 mr-2" onClick={() => goToOwner(o.id)}>
                            {o.name || o.id.slice(0,8)}
                          </button>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          {/* Orphan tier accounts */}
          <Section
            icon={Landmark}
            title="PCMN - Tier orphelins"
            count={report.pcmn_accounts.orphan_tier_accounts.length}
            severity={report.pcmn_accounts.orphan_tier_accounts.length ? 'warn' : 'ok'}
          >
            {report.pcmn_accounts.orphan_tier_accounts.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun compte tier orphelin</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gk_cleanup_orphan_tiers --copro-id XXX --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Compte</TableHead>
                      <TableHead className="text-xs">Nom</TableHead>
                      <TableHead className="text-xs w-16">ACP</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.pcmn_accounts.orphan_tier_accounts.slice(0, 10).map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">{r.account}</TableCell>
                        <TableCell className="text-xs">{r.name || '(sans nom)'}</TableCell>
                        <TableCell className="text-xs text-slate-400">{r.copro_id.slice(0,8)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Duplicated bank accounts */}
          <Section
            icon={Landmark}
            title="PCMN - Bank duplique"
            count={report.pcmn_accounts.duplicated_bank_accounts.length}
            severity={report.pcmn_accounts.duplicated_bank_accounts.length ? 'warn' : 'ok'}
          >
            {report.pcmn_accounts.duplicated_bank_accounts.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun compte bancaire duplique</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gm_merge_bank_accounts --copro-id XXX --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Comptes</TableHead>
                      <TableHead className="text-xs w-16">ACP</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.pcmn_accounts.duplicated_bank_accounts.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">
                          {r.accounts.map(a => `${a.number} (${a.name})`).join(' + ')}
                        </TableCell>
                        <TableCell className="text-xs text-slate-400">{r.copro_id.slice(0,8)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Credit notes without entry */}
          <Section
            icon={FileText}
            title="NC sans ecriture AC"
            count={report.credit_notes_without_entry.length}
            severity={report.credit_notes_without_entry.length ? 'err' : 'ok'}
          >
            {report.credit_notes_without_entry.length === 0 ? (
              <p className="text-xs text-slate-500">Toutes les notes de credit ont leur ecriture</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gl_heal_credit_notes --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Ref</TableHead>
                      <TableHead className="text-xs">Fournisseur</TableHead>
                      <TableHead className="text-xs w-24">Montant</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.credit_notes_without_entry.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">{r.internal_reference}</TableCell>
                        <TableCell className="text-xs">{r.supplier}</TableCell>
                        <TableCell className="text-xs font-mono text-red-600">{r.amount}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* iter90gn : Documents without GridFS - eviter la perte de fichiers au redeploy */}
          <Section
            icon={FileWarning}
            title="Documents sans GridFS"
            count={report.documents_without_gridfs?.length || 0}
            severity={(report.documents_without_gridfs?.length || 0) > 0 ? 'err' : 'ok'}
          >
            {(report.documents_without_gridfs?.length || 0) === 0 ? (
              <p className="text-xs text-slate-500">Tous les documents sont persistes en GridFS</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  <strong>Attention</strong> : ces documents ont leur fichier sur filesystem ephemere (perte au redeploiement K8s).
                  Re-uploadez-les ou supprimez-les via l&apos;UI documents.
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Titre</TableHead>
                      <TableHead className="text-xs w-32">Date</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.documents_without_gridfs.map((d, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs">{d.title || d.document_id.slice(0,12)}</TableCell>
                        <TableCell className="text-xs text-slate-400">{d.created_at?.slice(0,10)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>
        </div>
      )}
    </div>
  );
}
