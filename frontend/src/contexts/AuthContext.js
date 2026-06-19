import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;
const AuthContext = createContext(null);

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedCopro, setSelectedCoproState] = useState(() => {
    try { return localStorage.getItem('selectedCopro') || ''; } catch { return ''; }
  });

  // Filtre global par exercice fiscal (applique a toutes les pages : journaux,
  // banque, factures, appels, balances). Persiste dans localStorage par ACP.
  // Valeur speciale '' = "Tous les exercices" (pas de filtre).
  const [fiscalYears, setFiscalYears] = useState([]);
  const [selectedFiscalYearId, setSelectedFiscalYearIdState] = useState('');

  const setSelectedCopro = (id) => {
    setSelectedCoproState(id);
    try { localStorage.setItem('selectedCopro', id || ''); } catch { /* noop */ }
  };

  const setSelectedFiscalYearId = (id) => {
    setSelectedFiscalYearIdState(id || '');
    if (selectedCopro) {
      try {
        localStorage.setItem(`selectedFY:${selectedCopro}`, id || '');
      } catch { /* noop */ }
    }
  };

  // Charger les exercices fiscaux de l'ACP courante et choisir un default
  useEffect(() => {
    if (!selectedCopro || !user) {
      setFiscalYears([]);
      setSelectedFiscalYearIdState('');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/fiscal/years?copropriete_id=${selectedCopro}`,
          { withCredentials: true }
        );
        if (cancelled) return;
        const years = Array.isArray(data) ? data : [];
        setFiscalYears(years);
        // Restaurer le choix utilisateur si persiste
        let saved = '';
        try { saved = localStorage.getItem(`selectedFY:${selectedCopro}`) || ''; } catch { /* noop */ }
        if (saved && years.some(y => y.id === saved)) {
          setSelectedFiscalYearIdState(saved);
          return;
        }
        // Defaut : exercice en cours = celui dont aujourd'hui est dans la periode
        // ET status = 'open'. Sinon : le plus recent ouvert. Sinon vide.
        const todayIso = new Date().toISOString().slice(0, 10);
        const current = years.find(y =>
          y.status === 'open' &&
          (y.start_date || '') <= todayIso &&
          (y.end_date || '9999-12-31') >= todayIso
        );
        if (current) {
          setSelectedFiscalYearIdState(current.id);
          return;
        }
        const lastOpen = years
          .filter(y => y.status === 'open')
          .sort((a, b) => (b.start_date || '').localeCompare(a.start_date || ''))[0];
        if (lastOpen) {
          setSelectedFiscalYearIdState(lastOpen.id);
          return;
        }
        setSelectedFiscalYearIdState('');
      } catch {
        setFiscalYears([]);
        setSelectedFiscalYearIdState('');
      }
    })();
    return () => { cancelled = true; };
  }, [selectedCopro, user]);

  // Helper derive : objet FY courant
  const selectedFiscalYear = fiscalYears.find(y => y.id === selectedFiscalYearId) || null;

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/auth/me`, { withCredentials: true });
      setUser(data);
    } catch {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const login = async (email, password) => {
    const { data } = await axios.post(`${API}/api/auth/login`, { email, password }, { withCredentials: true });
    setUser(data);
    return data;
  };

  const register = async (email, password, name) => {
    const { data } = await axios.post(`${API}/api/auth/register`, { email, password, name }, { withCredentials: true });
    setUser(data);
    return data;
  };

  const logout = async () => {
    await axios.post(`${API}/api/auth/logout`, {}, { withCredentials: true });
    setUser(false);
  };

  const isSuperadmin = user && (user.role === 'superadmin' || user.role === 'admin');
  const isAdmin = user && (user.role === 'superadmin' || user.role === 'admin' || user.role === 'syndic');
  const isManager = user && (user.role === 'superadmin' || user.role === 'admin' || user.role === 'syndic' || user.role === 'gestionnaire');
  const isOwner = user && user.role === 'owner';

  return (
    <AuthContext.Provider value={{
      user, loading, login, register, logout,
      refreshUser: checkAuth,
      selectedCopro, setSelectedCopro,
      fiscalYears, selectedFiscalYearId, setSelectedFiscalYearId, selectedFiscalYear,
      isAdmin, isSuperadmin, isManager, isOwner,
    }}>
      {children}
    </AuthContext.Provider>
  );
}
