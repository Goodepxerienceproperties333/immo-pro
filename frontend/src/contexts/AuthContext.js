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

  const setSelectedCopro = (id) => {
    setSelectedCoproState(id);
    try { localStorage.setItem('selectedCopro', id || ''); } catch {}
  };

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

  const isAdmin = user && (user.role === 'superadmin' || user.role === 'admin');
  const isManager = user && (user.role === 'superadmin' || user.role === 'admin' || user.role === 'syndic');

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, selectedCopro, setSelectedCopro, isAdmin, isManager }}>
      {children}
    </AuthContext.Provider>
  );
}
