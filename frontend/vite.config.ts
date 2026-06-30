import { readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const __dirname = dirname(fileURLToPath(import.meta.url));
let appVersion = '0.0.0';
try {
  appVersion = readFileSync(resolve(__dirname, '../VERSION'), 'utf-8').trim();
} catch {}

const basePath = (process.env.FLYMAIL_BASE_PATH || '/').replace(/\/+$/, '') || '/';

const devOnlyProxy = {
  '/oauth/outlook/callback': {
    target: 'http://localhost:51010',
    changeOrigin: true,
    rewrite: () => '/api/auth/callback',
  },
  '/oauth/gmail/callback': {
    target: 'http://localhost:51010',
    changeOrigin: true,
    rewrite: () => '/api/auth/callback',
  },
};

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  base: basePath.endsWith('/') ? basePath : `${basePath}/`,
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion),
  },
  build: {
    outDir: '../dist/ui',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      ...(command === 'serve' ? devOnlyProxy : {}),
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
    },
  },
}));
