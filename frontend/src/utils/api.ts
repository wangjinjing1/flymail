import axios from 'axios';

const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
const apiBase = `${basePath || ''}/api`;

const api = axios.create({
  baseURL: apiBase,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.response.use(
  (response) => response.data,
  (error) => Promise.reject(error.response?.data || error),
);

export default api;
