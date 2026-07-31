import { useState, useEffect, useCallback, useRef } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { toast } from 'sonner';
import { Plus, Trash2, Pencil, FileText, Tag, Upload, Loader2, Download, Sparkles, Eye } from 'lucide-react';
import DocumentViewerModal from '@/components/DocumentViewerModal';

export default function DocumentsPage() {
  const [tab, setTab] = useState('documents');
  const [documents, setDocuments] = useState([]);
  const [categories, setCategories] = useState([]);
  const [filterCategory, setFilterCategory] = useState('all');
  const [docDialog, setDocDialog] = useState(false);
  const [editingDoc, setEditingDoc] = useState(null); // iter90ht : id du doc en cours d'edition
  const [catDialog, setCatDialog] = useState(false);
  const [editingCat, setEditingCat] = useState(null);
  const [docForm, setDocForm] = useState({ title: '', description: '', category_id: '', content: '' });
  const [catForm, setCatForm] = useState({ name: '', description: '' });
  const [uploading, setUploading] = useState(false);
  const [viewerDoc, setViewerDoc] = useState(null); // Document en cours de visualisation
  const fileInputRef = useRef(null);

  const load = useCallback(async () => {
    const [d, c] = await Promise.all([
      api.get('/documents', { params: filterCategory && filterCategory !== 'all' ? { category_id: filterCategory } : {} }),
      api.get('/documents/categories')
    ]);
    setDocuments(d.data); setCategories(c.data);
  }, [filterCategory]);

  useEffect(() => { load(); }, [load]);

  const getCatName = (id) => categories.find(c => c.id === id)?.name || '-';

  // Document handlers
  const openCreateDoc = () => { setEditingDoc(null); setDocForm({ title: '', description: '', category_id: '', content: '' }); setDocDialog(true); };
  // iter90ht : ouvre le dialog en mode edition pour reclasser un doc
  const openEditDoc = (doc) => {
    setEditingDoc(doc);
    setDocForm({
      title: doc.title || '',
      description: doc.description || '',
      category_id: doc.category_id || '',
      content: doc.content || '',
    });
    setDocDialog(true);
  };
  const saveDoc = async () => {
    try {
      if (editingDoc) {
        // iter90ht : PUT pour modifier le doc existant (titre, description, categorie)
        await api.put(`/documents/${editingDoc.id}`, docForm);
        toast.success('Document modifie');
      } else {
        await api.post('/documents', docForm);
        toast.success('Document cree');
      }
      setDocDialog(false); setEditingDoc(null); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteDoc = async (id) => {
    if (!window.confirm('Supprimer ce document ?')) return;
    await api.delete(`/documents/${id}`); toast.success('Document supprime'); load();
  };

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const coproId = localStorage.getItem('selectedCopro');
    if (!coproId) { toast.error('Selectionnez une copropriete'); return; }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('copropriete_id', coproId);
      fd.append('auto_classify', 'true');
      const { data } = await api.post('/documents/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      const aiMsg = data.ai_classification?.category ? ` (IA: ${data.ai_classification.category})` : '';
      toast.success(`Document importe${aiMsg}`);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur upload');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // Category handlers
  const openCreateCat = () => { setEditingCat(null); setCatForm({ name: '', description: '' }); setCatDialog(true); };
  const openEditCat = (cat) => { setEditingCat(cat); setCatForm({ name: cat.name, description: cat.description || '' }); setCatDialog(true); };
  const saveCat = async () => {
    try {
      if (editingCat) { await api.put(`/documents/categories/${editingCat.id}`, catForm); toast.success('Categorie modifiee'); }
      else { await api.post('/documents/categories', catForm); toast.success('Categorie creee'); }
      setCatDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteCat = async (id) => {
    if (!window.confirm('Supprimer cette categorie ?')) return;
    await api.delete(`/documents/categories/${id}`); toast.success('Categorie supprimee'); load();
  };

  return (
    <div data-testid="documents-page">
      <div className="page-header"><h1 className="page-title">Documents</h1><p className="page-subtitle">Gestion des documents de la copropriete</p></div>

      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex items-center justify-between mb-4">
          <TabsList data-testid="documents-tabs">
            <TabsTrigger value="documents"><FileText size={14} className="mr-2" /> Documents</TabsTrigger>
            <TabsTrigger value="categories"><Tag size={14} className="mr-2" /> Categories</TabsTrigger>
          </TabsList>
          <div className="flex gap-2">
            {tab === 'documents' && (
              <>
                <Button onClick={openCreateDoc} variant="outline" data-testid="create-doc-btn"><Plus size={16} className="mr-2" /> Note manuelle</Button>
                <input type="file" ref={fileInputRef} onChange={handleFileChange} accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.heif" className="hidden" data-testid="doc-file-input" />
                <Button onClick={() => fileInputRef.current?.click()} disabled={uploading} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="upload-doc-btn">
                  {uploading ? <><Loader2 size={16} className="mr-2 animate-spin" /> Analyse IA en cours...</> : <><Upload size={16} className="mr-2" /> Importer fichier (auto-classement IA)</>}
                </Button>
              </>
            )}
            {tab === 'categories' && (
              <Button onClick={openCreateCat} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-cat-btn"><Plus size={16} className="mr-2" /> Nouvelle categorie</Button>
            )}
          </div>
        </div>

        <TabsContent value="documents" className="mt-0">
          <div className="flex items-center mb-4 gap-4 flex-wrap">
            <Select value={filterCategory} onValueChange={setFilterCategory}>
              <SelectTrigger className="w-[250px]" data-testid="filter-category"><SelectValue placeholder="Toutes les categories" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Toutes les categories</SelectItem>
                {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          {documents.length === 0 ? (
            <Card className="border-slate-200"><CardContent className="p-8 text-center text-slate-400">Aucun document - importez votre premier fichier (PDF ou image)</CardContent></Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {documents.map(doc => (
                <Card key={doc.id} className="border-slate-200 hover:shadow-md transition-shadow" data-testid={`doc-card-${doc.id}`}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between mb-2">
                      <button
                        type="button"
                        onClick={() => (doc.gridfs_id || doc.stored_path) && setViewerDoc(doc)}
                        className="flex items-center gap-2 min-w-0 text-left hover:text-[#1D4ED8] group"
                        title={(doc.gridfs_id || doc.stored_path) ? 'Cliquer pour visualiser' : 'Aucun fichier associe'}
                        data-testid={`doc-open-${doc.id}`}
                      >
                        <FileText size={16} className="text-[#022D52] flex-shrink-0 group-hover:text-[#1D4ED8]" />
                        <span className="font-medium text-sm text-slate-900 truncate group-hover:text-[#1D4ED8]">{doc.title}</span>
                      </button>
                      <div className="flex gap-0 flex-shrink-0">
                        {(doc.stored_path || doc.gridfs_id) && (
                          <>
                            <Button variant="ghost" size="sm" onClick={() => setViewerDoc(doc)} className="h-6 w-6 p-0 text-slate-400 hover:text-[#022D52]" title="Visualiser" data-testid={`doc-view-${doc.id}`}>
                              <Eye size={12} />
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => window.open(`${process.env.REACT_APP_BACKEND_URL}/api/documents/${doc.id}/download`, '_blank')} className="h-6 w-6 p-0 text-slate-400" title="Telecharger" data-testid={`doc-download-${doc.id}`}>
                              <Download size={12} />
                            </Button>
                          </>
                        )}
                        {/* iter90ht : bouton edition pour reclasser / renommer */}
                        <Button variant="ghost" size="sm" onClick={() => openEditDoc(doc)} className="h-6 w-6 p-0 text-slate-400 hover:text-[#022D52]" title="Modifier" data-testid={`doc-edit-${doc.id}`}>
                          <Pencil size={12} />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => deleteDoc(doc.id)} className="text-red-400 h-6 w-6 p-0" data-testid={`doc-delete-${doc.id}`}><Trash2 size={12} /></Button>
                      </div>
                    </div>
                    {doc.description && <p className="text-xs text-slate-500 mb-2 line-clamp-2">{doc.description}</p>}
                    {doc.ai_classification?.category && (
                      <div className="flex items-center gap-1 mb-2 text-[10px] text-purple-600">
                        <Sparkles size={10} />
                        <span>Auto-classifie</span>
                        {doc.doc_date && <span className="text-slate-400">- {doc.doc_date}</span>}
                      </div>
                    )}
                    {/* iter90hs : indicateur source Communication */}
                    {doc.source === 'communication' && (
                      <div className="flex items-center gap-1 mb-2 text-[10px] text-blue-600">
                        <span className="inline-block w-1.5 h-1.5 bg-blue-500 rounded-full" />
                        <span>Piece jointe communication</span>
                      </div>
                    )}
                    <div className="flex items-center justify-between">
                      <Badge variant="outline" className="text-[10px]">{getCatName(doc.category_id)}</Badge>
                      <span className="text-[10px] text-slate-400">{doc.created_at?.split('T')[0]}</span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="categories" className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Nom</TableHead><TableHead>Description</TableHead><TableHead className="w-24">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {categories.length === 0 ? (
                  <TableRow><TableCell colSpan={3} className="text-center py-8 text-slate-400">Aucune categorie</TableCell></TableRow>
                ) : categories.map(cat => (
                  <TableRow key={cat.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-medium">{cat.name}</TableCell>
                    <TableCell className="text-slate-600">{cat.description}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openEditCat(cat)}><Pencil size={14} /></Button>
                        <Button variant="ghost" size="sm" onClick={() => deleteCat(cat.id)} className="text-red-500"><Trash2 size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      {/* Document Dialog */}
      <Dialog open={docDialog} onOpenChange={(open) => { setDocDialog(open); if (!open) setEditingDoc(null); }}>
        <DialogContent data-testid="doc-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingDoc ? 'Modifier le document' : 'Nouveau document'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Titre *</label><Input value={docForm.title} onChange={e => setDocForm({...docForm, title: e.target.value})} data-testid="doc-title" /></div>
            <div><label className="form-label">Categorie</label>
              <Select value={docForm.category_id || 'none'} onValueChange={v => setDocForm({...docForm, category_id: v === 'none' ? '' : v})}>
                <SelectTrigger data-testid="doc-category-select"><SelectValue placeholder="Selectionner" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Aucune</SelectItem>
                  {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div><label className="form-label">Description</label><Input value={docForm.description} onChange={e => setDocForm({...docForm, description: e.target.value})} data-testid="doc-description" /></div>
            <div><label className="form-label">Contenu (note interne)</label><Textarea value={docForm.content} onChange={e => setDocForm({...docForm, content: e.target.value})} rows={4} data-testid="doc-content" /></div>
            {editingDoc?.filename && (
              <div className="text-xs text-slate-500 border-l-2 border-slate-200 pl-3">
                Fichier : <b>{editingDoc.filename}</b> {editingDoc.size_bytes ? `- ${Math.round(editingDoc.size_bytes/1024)} Ko` : ''}
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => { setDocDialog(false); setEditingDoc(null); }} data-testid="doc-cancel-btn">Annuler</Button>
              <Button onClick={saveDoc} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="doc-save-btn">{editingDoc ? 'Enregistrer' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Category Dialog */}
      <Dialog open={catDialog} onOpenChange={setCatDialog}>
        <DialogContent data-testid="cat-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingCat ? 'Modifier categorie' : 'Nouvelle categorie'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={catForm.name} onChange={e => setCatForm({...catForm, name: e.target.value})} data-testid="cat-name" /></div>
            <div><label className="form-label">Description</label><Input value={catForm.description} onChange={e => setCatForm({...catForm, description: e.target.value})} /></div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setCatDialog(false)}>Annuler</Button>
              <Button onClick={saveCat} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="cat-save-btn">{editingCat ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {/* Document Viewer Modal (iter93ct) */}
      <DocumentViewerModal
        open={!!viewerDoc}
        onClose={() => setViewerDoc(null)}
        doc={viewerDoc}
      />
    </div>
  );
}
