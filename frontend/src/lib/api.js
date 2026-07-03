import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

const api = axios.create({
  baseURL: `${API}/api`,
  withCredentials: true,
});

// Chinese-wall interceptor: scope every request by the currently selected ACP.
// Global resources (suppliers, users) are excluded from auto-scoping.
// iter85k : /owners RETIRE de cette liste -> est desormais scopa par defaut
// sur l'ACP courante (filet de securite pour OwnersPage et tout autre consumer).
// Si un appelant a besoin de TOUS les owners (ex. CoproprietesPage pour
// l'autocomplete a la creation d'ACP), il doit passer explicitement
// `params.copropriete_id = 'all'` ou retirer l'header X-Copropriete-Id.
const GLOBAL_PATH_PREFIXES = ['/suppliers', '/users', '/auth', '/coproprietes', '/banking/lookup', '/legal', '/admin/duplicates'];
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
    // Normalize Pydantic / FastAPI validation errors so `detail` is ALWAYS a string.
    // FastAPI returns 422 as { detail: [{ type, loc, msg, input, url }, ...] }
    // which breaks any naive `toast.error(err.response.data.detail)` (React: "Objects
    // are not valid as a React child").
    try {
      if (error?.response?.data?.detail) {
        const d = error.response.data.detail;
        if (Array.isArray(d)) {
          error.response.data.detail = d.map(e => {
            if (typeof e === 'string') return e;
            const loc = Array.isArray(e?.loc) ? e.loc.filter(x => x !== 'body').join('.') : '';
            const msg = e?.msg || JSON.stringify(e);
            return loc ? `${loc}: ${msg}` : msg;
          }).join(' | ');
        } else if (typeof d === 'object') {
          error.response.data.detail = d.message || d.msg || JSON.stringify(d);
        }
      }
    } catch {}
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

/**
 * Robust formatter for FastAPI / Pydantic error responses.
 * FastAPI Pydantic validation errors come back as an ARRAY of objects:
 *   { type, loc, msg, input, url }
 * Rendering them directly in JSX throws "Objects are not valid as a React child".
 * This helper always returns a string.
 */
export function extractApiError(err, fallback = 'Une erreur est survenue.') {
  const d = err?.response?.data?.detail;
  if (d == null) return err?.message || fallback;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map(e => {
      if (typeof e === 'string') return e;
      const loc = Array.isArray(e?.loc) ? e.loc.join('.') : '';
      const msg = e?.msg || JSON.stringify(e);
      return loc ? `${loc}: ${msg}` : msg;
    }).join(' | ');
  }
  if (typeof d === 'object') {
    return d.message || d.msg || JSON.stringify(d);
  }
  return String(d);
}

export default api;
