import { useState, useEffect, useCallback } from 'react';
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
import { Plus, Trash2, Pencil, FolderOpen, FileText, Tag } from 'lucide-react';

export default function DocumentsPage() {
  const [tab, setTab] = useState('documents');
  const [documents, setDocuments] = useState([]);
  const [categories, setCategories] = useState([]);
  const [filterCategory, setFilterCategory] = useState('all');
  const [docDialog, setDocDialog] = useState(false);
  const [catDialog, setCatDialog] = useState(false);
  const [editingCat, setEditingCat] = useState(null);
  const [docForm, setDocForm] = useState({ title: '', description: '', category_id: '', content: '' });
  const [catForm, setCatForm] = useState({ name: '', description: '' });

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
  const openCreateDoc = () => { setDocForm({ title: '', description: '', category_id: '', content: '' }); setDocDialog(true); };
  const saveDoc = async () => {
    try {
      await api.post('/documents', docForm);
      toast.success('Document cree'); setDocDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteDoc = async (id) => {
    if (!window.confirm('Supprimer ce document ?')) return;
    await api.delete(`/documents/${id}`); toast.success('Document supprime'); load();
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
        <TabsList className="mb-4" data-testid="documents-tabs">
          <TabsTrigger value="documents"><FileText size={14} className="mr-2" /> Documents</TabsTrigger>
          <TabsTrigger value="categories"><Tag size={14} className="mr-2" /> Categories</TabsTrigger>
        </TabsList>

        <TabsContent value="documents" className="mt-0">
          <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
            <Select value={filterCategory} onValueChange={setFilterCategory}>
              <SelectTrigger className="w-[250px]" data-testid="filter-category"><SelectValue placeholder="Toutes les categories" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Toutes les categories</SelectItem>
                {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button onClick={openCreateDoc} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-doc-btn"><Plus size={16} className="mr-2" /> Nouveau document</Button>
          </div>
          {documents.length === 0 ? (
            <Card className="border-slate-200"><CardContent className="p-8 text-center text-slate-400">Aucun document</CardContent></Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {documents.map(doc => (
                <Card key={doc.id} className="border-slate-200 hover:shadow-md transition-shadow" data-testid={`doc-card-${doc.id}`}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <FileText size={16} className="text-[#0055FF] flex-shrink-0" />
                        <span className="font-medium text-sm text-slate-900">{doc.title}</span>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => deleteDoc(doc.id)} className="text-red-400 h-6 w-6 p-0"><Trash2 size={12} /></Button>
                    </div>
                    {doc.description && <p className="text-xs text-slate-500 mb-2 line-clamp-2">{doc.description}</p>}
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
          <div className="flex justify-end mb-4">
            <Button onClick={openCreateCat} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-cat-btn"><Plus size={16} className="mr-2" /> Nouvelle categorie</Button>
          </div>
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
      <Dialog open={docDialog} onOpenChange={setDocDialog}>
        <DialogContent data-testid="doc-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouveau document</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Titre *</label><Input value={docForm.title} onChange={e => setDocForm({...docForm, title: e.target.value})} data-testid="doc-title" /></div>
            <div><label className="form-label">Categorie</label>
              <Select value={docForm.category_id} onValueChange={v => setDocForm({...docForm, category_id: v})}>
                <SelectTrigger><SelectValue placeholder="Selectionner" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Aucune</SelectItem>
                  {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div><label className="form-label">Description</label><Input value={docForm.description} onChange={e => setDocForm({...docForm, description: e.target.value})} /></div>
            <div><label className="form-label">Contenu</label><Textarea value={docForm.content} onChange={e => setDocForm({...docForm, content: e.target.value})} rows={4} /></div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDocDialog(false)}>Annuler</Button>
              <Button onClick={saveDoc} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="doc-save-btn">Creer</Button>
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
              <Button onClick={saveCat} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="cat-save-btn">{editingCat ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
