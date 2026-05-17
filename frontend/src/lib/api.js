import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const api = axios.create({
  baseURL: `${API}/api`,
  withCredentials: true,
});

// Chinese-wall interceptor: scope every request by the currently selected ACP.
// Global resources (owners, suppliers, users) are excluded from auto-scoping.
const GLOBAL_PATH_PREFIXES = ['/owners', '/suppliers', '/users', '/auth', '/coproprietes', '/banking/lookup'];
const isGlobalPath = (url = '') => GLOBAL_PATH_PREFIXES.some(p => url === p || url.startsWith(p + '/') || url.startsWith(p + '?'));

api.interceptors.request.use((config) => {
  try {
    const coproId = localStorage.getItem('selectedCopro');
    const url = config.url || '';
    if (coproId && coproId !== 'all' && coproId !== '' && !isGlobalPath(url)) {
      config.headers = config.headers || {};
      config.headers['X-Copropriete-Id'] = coproId;
      const method = (config.method || 'get').toLowerCase();
      if (method === 'get') {
        config.params = config.params || {};
        if (!config.params.copropriete_id) {
          config.params.copropriete_id = coproId;
        }
      } else if (['post', 'put', 'patch'].includes(method)) {
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

// Response interceptor: try silent refresh on 401, otherwise redirect to login.
let isRefreshing = false;
let refreshQueue = [];

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config || {};
    const status = error.response?.status;
    const isAuthEndpoint = (original.url || '').includes('/auth/');
    if (status === 401 && !original._retry && !isAuthEndpoint) {
      original._retry = true;
      if (isRefreshing) {
        // Queue until refresh completes
        return new Promise((resolve, reject) => {
          refreshQueue.push({ resolve, reject, original });
        }).then((cfg) => api(cfg)).catch((e) => Promise.reject(e));
      }
      isRefreshing = true;
      try {
        await axios.post(`${API}/api/auth/refresh`, {}, { withCredentials: true });
        // Replay queued requests
        refreshQueue.forEach(({ resolve, original: cfg }) => resolve(cfg));
        refreshQueue = [];
        return api(original);
      } catch (refreshErr) {
        refreshQueue.forEach(({ reject }) => reject(refreshErr));
        refreshQueue = [];
        try {
          if (!window.location.pathname.startsWith('/login')) {
            window.location.href = '/login';
          }
        } catch {}
        return Promise.reject(refreshErr);
      } finally {
        isRefreshing = false;
      }
    }
    return Promise.reject(error);
  }
);

export default api;
