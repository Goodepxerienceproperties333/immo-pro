import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const api = axios.create({
  baseURL: `${API}/api`,
  withCredentials: true,
});

// Interceptor to add copropriete_id to all GET requests
api.interceptors.request.use((config) => {
  try {
    const coproId = localStorage.getItem('selectedCopro');
    if (coproId && coproId !== 'all' && coproId !== '') {
      if (config.method === 'get') {
        config.params = config.params || {};
        if (!config.params.copropriete_id) {
          config.params.copropriete_id = coproId;
        }
      }
    }
  } catch {}
  return config;
});

export default api;
