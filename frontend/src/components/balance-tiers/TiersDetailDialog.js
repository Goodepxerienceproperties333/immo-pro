import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { fmtDate } from '@/lib/dateFmt';

import { fmtEUR } from '@/lib/format';
/**
 * Dialog de detail proprietaire / fournisseur.
 * Affiche total debit/credit + solde + toggle vue resumee|detail par lot (iter90bv, owner only).
 */
export default function TiersDetailDialog({
  open, onClose, detail, detailType,
  detailGrouped, detailOwnerId, viewOwnerDetail,
}) {
  const isOwner = detailType === 'owner';
  const nameDisplay = isOwner ? detail?.owner?.name : detail?.supplier?.name;

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto" data-testid="tiers-detail-dialog">
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            Situation de compte: {nameDisplay}
          </DialogTitle>
          {isOwner && detail?.owner?.vcs_code && (
            <p className="font-mono text-sm text-[#022D52]">{detail.owner.vcs_code}</p>
          )}
        </DialogHeader>
        <div className="mt-2">
          <div className="flex gap-4 mb-4 text-sm items-center flex-wrap">
            <div><span className="text-slate-500">Total debit:</span> <span className="font-mono font-bold">{fmtEUR(detail?.total_debit)} EUR</span></div>
            <div><span className="text-slate-500">Total credit:</span> <span className="font-mono font-bold">{fmtEUR(detail?.total_credit)} EUR</span></div>
            <div>
              <span className="text-slate-500">Solde:</span>
              <span className={`font-mono font-bold ml-1 ${detail?.status === 'debiteur' ? 'text-red-700' : detail?.status === 'crediteur' ? (isOwner ? 'text-green-700' : 'text-orange-700') : 'text-slate-600'}`}>
                {fmtEUR(detail?.balance)} EUR
              </span>
              <Badge variant="outline" className="ml-2 text-[10px]">{detail?.status === 'debiteur' ? (isOwner ? 'Doit payer' : 'Trop-paye') : detail?.status === 'crediteur' ? (isOwner ? 'A rembourser' : 'A payer') : 'Solde'}</Badge>
            </div>
            {isOwner && (
              <div className="ml-auto flex items-center gap-1 bg-slate-100 rounded-full p-1" data-testid="detail-view-mode-toggle">
                <button
                  type="button"
                  onClick={() => viewOwnerDetail(detailOwnerId, true)}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition ${detailGrouped ? 'bg-white text-[#022D52] shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                  data-testid="detail-view-grouped-btn"
                  title="Fusionne les lignes portant sur plusieurs lots du meme proprietaire"
                >
                  Vue resumee
                </button>
                <button
                  type="button"
                  onClick={() => viewOwnerDetail(detailOwnerId, false)}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition ${!detailGrouped ? 'bg-white text-[#022D52] shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                  data-testid="detail-view-detailed-btn"
                  title="Affiche une ligne par lot (utile pour audit)"
                >
                  Detail par lot
                </button>
              </div>
            )}
          </div>
          <div className="border rounded-md overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead className="w-24">Date</TableHead><TableHead>Description</TableHead><TableHead>Ref</TableHead>
                <TableHead className="text-right w-28">Debit</TableHead><TableHead className="text-right w-28">Credit</TableHead><TableHead className="text-right w-28">Solde</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {(detail?.movements || []).map((m, i) => (
                  <TableRow
                    key={i}
                    className={m.is_reprise ? "bg-blue-50 hover:bg-blue-100/70 border-b-2 border-blue-200" : "hover:bg-slate-50/50"}
                    data-testid={m.is_reprise ? "tiers-reprise-row" : undefined}
                  >
                    <TableCell className="font-mono text-xs">{fmtDate(m.date)}</TableCell>
                    <TableCell className="text-sm">
                      {m.is_reprise && (
                        <Badge variant="outline" className="mr-2 text-[9px] bg-blue-100 text-blue-800 border-blue-300 font-semibold">
                          REPRISE
                        </Badge>
                      )}
                      {m.description}
                    </TableCell>
                    <TableCell className="text-xs text-slate-400">{m.reference}</TableCell>
                    <TableCell className={`text-right font-mono text-sm ${m.is_reprise ? 'font-semibold' : ''}`}>{m.debit > 0 ? fmtEUR(m.debit) : ''}</TableCell>
                    <TableCell className={`text-right font-mono text-sm ${m.is_reprise ? 'font-semibold' : ''}`}>{m.credit > 0 ? fmtEUR(m.credit) : ''}</TableCell>
                    <TableCell className={`text-right font-mono text-sm font-semibold ${m.running_balance > 0 ? 'text-red-700' : m.running_balance < 0 ? 'text-green-700' : ''}`}>{fmtEUR(m.running_balance)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
