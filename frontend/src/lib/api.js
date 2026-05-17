import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const api = axios.create({
  baseURL: `${API}/api`,
  withCredentials: true,
});

// Chinese-wall interceptor: scope every request by the currently selected ACP.
// - GET: add copropriete_id as query param
// - POST/PUT/PATCH: inject copropriete_id into JSON body (unless already set)
// - Header X-Copropriete-Id sent on every request for backend awareness
api.interceptors.request.use((config) => {
  try {
    const coproId = localStorage.getItem('selectedCopro');
    if (coproId && coproId !== 'all' && coproId !== '') {
      config.headers = config.headers || {};
      config.headers['X-Copropriete-Id'] = coproId;
      const method = (config.method || 'get').toLowerCase();
      if (method === 'get') {
        config.params = config.params || {};
        if (!config.params.copropriete_id) {
          config.params.copropriete_id = coproId;
        }
      } else if (['post', 'put', 'patch'].includes(method)) {
        // Inject into body when body is a plain object (not FormData)
        if (config.data && typeof config.data === 'object' && !(config.data instanceof FormData)) {
          if (config.data.copropriete_id === undefined || config.data.copropriete_id === '' || config.data.copropriete_id === null) {
            config.data = { ...config.data, copropriete_id: coproId };
          }
        } else if (config.data === undefined || config.data === null) {
          config.data = { copropriete_id: coproId };
        }
      }
    }
  } catch {}
  return config;
});

export default api;
