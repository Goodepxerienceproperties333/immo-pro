import { useMemo } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Download, ExternalLink, FileText } from 'lucide-react';

/**
 * Visionneuse de document unifiee (syndic + proprietaire).
 * - PDF   : iframe inline via ?inline=1
 * - Image : <img> via ?inline=1
 * - Autre : message + bouton telecharger
 *
 * Cookies de session sont envoyes automatiquement par le navigateur puisque
 * l'URL cible /api/... est sur le meme domaine que le frontend (K8s ingress).
 */
export default function DocumentViewerModal({ open, onClose, doc }) {
  const backendUrl = process.env.REACT_APP_BACKEND_URL;

  const { previewUrl, downloadUrl, kind } = useMemo(() => {
    if (!doc) return { previewUrl: '', downloadUrl: '', kind: 'none' };
    const base = `${backendUrl}/api/documents/${doc.id}/download`;
    const mime = (doc.mime_type || '').toLowerCase();
    const filename = (doc.filename || '').toLowerCase();
    const isPdf = mime === 'application/pdf' || filename.endsWith('.pdf');
    const isImage = mime.startsWith('image/') || /\.(png|jpe?g|webp|gif|heic|heif)$/.test(filename);
    return {
      previewUrl: `${base}?inline=1`,
      downloadUrl: base,
      kind: isPdf ? 'pdf' : isImage ? 'image' : 'other',
    };
  }, [doc, backendUrl]);

  if (!doc) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent
        className="max-w-6xl w-[95vw] h-[92vh] p-0 gap-0 flex flex-col"
        data-testid="document-viewer-modal"
      >
        <DialogHeader className="px-4 py-3 border-b border-slate-200 flex-row items-center gap-3 space-y-0">
          <div className="w-9 h-9 rounded-md bg-blue-50 flex items-center justify-center flex-shrink-0">
            <FileText size={18} className="text-[#022D52]" />
          </div>
          <div className="flex-1 min-w-0">
            <DialogTitle
              className="text-sm font-semibold text-slate-900 truncate text-left"
              style={{ fontFamily: 'Chivo,sans-serif' }}
              data-testid="viewer-title"
            >
              {doc.title || doc.filename || 'Document'}
            </DialogTitle>
            <div className="flex items-center gap-2 text-[11px] text-slate-500 mt-0.5">
              {doc.filename && <span className="truncate max-w-[280px]">{doc.filename}</span>}
              {doc.size_bytes ? <span>- {Math.round(doc.size_bytes / 1024)} Ko</span> : null}
              {doc.mime_type && (
                <Badge variant="outline" className="text-[10px] py-0 h-4">{doc.mime_type}</Badge>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0 pr-6">
            <a
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md text-slate-600 hover:bg-slate-100"
              title="Ouvrir dans un nouvel onglet"
              data-testid="viewer-open-new-tab"
            >
              <ExternalLink size={13} /> Onglet
            </a>
            <a
              href={downloadUrl}
              className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md bg-[#022D52] hover:bg-[#1D4ED8] text-white"
              title="Telecharger"
              data-testid="viewer-download"
            >
              <Download size={13} /> Telecharger
            </a>
          </div>
        </DialogHeader>

        <div className="flex-1 min-h-0 bg-slate-100">
          {kind === 'pdf' && (
            <iframe
              src={previewUrl}
              title={doc.title || 'PDF'}
              className="w-full h-full border-0 bg-white"
              data-testid="viewer-pdf-frame"
            />
          )}
          {kind === 'image' && (
            <div className="w-full h-full flex items-center justify-center overflow-auto p-4">
              <img
                src={previewUrl}
                alt={doc.title || 'Image'}
                className="max-w-full max-h-full object-contain shadow-lg bg-white"
                data-testid="viewer-image"
              />
            </div>
          )}
          {kind === 'other' && (
            <div className="w-full h-full flex flex-col items-center justify-center gap-4 p-8 text-center">
              <FileText size={48} className="text-slate-300" />
              <div>
                <div className="font-medium text-slate-700 mb-1">Apercu non disponible</div>
                <div className="text-sm text-slate-500 mb-4">
                  Ce type de fichier ne peut pas etre affiche directement dans le navigateur.
                </div>
                <a
                  href={downloadUrl}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-[#022D52] hover:bg-[#1D4ED8] text-white text-sm"
                  data-testid="viewer-fallback-download"
                >
                  <Download size={15} /> Telecharger le fichier
                </a>
              </div>
            </div>
          )}
          {kind === 'none' && (
            <div className="w-full h-full flex items-center justify-center text-slate-400">
              Aucun fichier associe a ce document
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
