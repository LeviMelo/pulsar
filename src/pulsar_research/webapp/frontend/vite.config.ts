import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* The build lands in ../static, which is what the Python server already serves
 * and what the wheel already ships. Keeping that path means `pulsar serve`
 * needs no knowledge that a build step exists: it finds an index.html and a
 * hashed asset bundle exactly where it used to find hand-written modules.
 *
 * In development `npm run dev` serves the app from Vite with hot reload and
 * proxies /api to the Python console, so the two halves are developed
 * independently without CORS or a second origin in the browser.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    // No source map in the shipped bundle. It is 1.3 MB that would be rewritten
    // on every build, and the committed output lives beside the source that
    // produced it; `npm run dev` serves the real modules, which is where you
    // would be debugging anyway.
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8787', changeOrigin: false },
    },
  },
});
