import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Search } from 'lucide-react';

const CLASS_NAMES = {
  1: 'Capitaux propres',
  2: 'Immobilisations',
  4: 'Creances et dettes',
  5: 'Tresorerie',
  6: 'Charges',
  7: 'Produits',
};

export default function AccountingPage() {
  const [accounts, setAccounts] = useState([]);
  const [search, setSearch] = useState('');
  const [activeClass, setActiveClass] = useState('all');

  const load = useCallback(async () => {
    const params = {};
    if (search) params.search = search;
    if (activeClass !== 'all') params.class_num = Number(activeClass);
    const { data } = await api.get('/accounting/pcmn', { params });
    setAccounts(data);
  }, [search, activeClass]);

  useEffect(() => { load(); }, [load]);

  const classes = [...new Set(accounts.map(a => a.class_num))].sort();

  return (
    <div data-testid="accounting-page">
      <div className="page-header">
        <h1 className="page-title">Plan Comptable PCMN</h1>
        <p className="page-subtitle">Plan Comptable Minimum Normalise - Belgique</p>
      </div>

      <div className="flex flex-col sm:flex-row gap-4 mb-4">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Rechercher un compte..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="pcmn-search"
          />
        </div>
      </div>

      <Tabs value={activeClass} onValueChange={setActiveClass}>
        <TabsList className="mb-4 flex-wrap h-auto gap-1" data-testid="pcmn-class-tabs">
          <TabsTrigger value="all" className="text-xs">Tous</TabsTrigger>
          {Object.entries(CLASS_NAMES).map(([num, name]) => (
            <TabsTrigger key={num} value={num} className="text-xs">
              Cl. {num} - {name}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value={activeClass} className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-32">Numero</TableHead>
                  <TableHead>Libelle</TableHead>
                  <TableHead className="w-24">Classe</TableHead>
                  <TableHead className="w-24">Type</TableHead>
                  <TableHead className="w-32">Parent</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.length === 0 ? (
                  <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucun compte trouve</TableCell></TableRow>
                ) : accounts.map((acc, i) => {
                  const indent = acc.parent ? 'pl-8' : 'pl-4';
                  return (
                    <TableRow key={i} className="hover:bg-slate-50/50">
                      <TableCell className={`font-mono text-sm font-semibold text-slate-900 ${indent}`}>{acc.number}</TableCell>
                      <TableCell className={`text-slate-700 ${acc.parent ? 'text-sm' : 'font-medium'}`}>{acc.name}</TableCell>
                      <TableCell><Badge variant="outline" className="text-xs font-mono">{acc.class_num}</Badge></TableCell>
                      <TableCell>
                        <Badge className={acc.type === 'balance' ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-green-50 text-green-700 border-green-200'} variant="outline">
                          {acc.type === 'balance' ? 'Bilan' : 'Resultat'}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-slate-400">{acc.parent || '-'}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <div className="mt-2 text-xs text-slate-400">{accounts.length} comptes</div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
