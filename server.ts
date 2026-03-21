import express from 'express';
import path from 'path';

/**
 * Production static file server for UI Core.
 * In development, use `npx vite` instead — it proxies /api/* to FastAPI on :7070.
 * This file is only needed for serving the built SPA in production.
 */
async function startServer() {
  const app = express();
  const PORT = Number(process.env.UI_PORT || 8080);

  const distPath = path.join(process.cwd(), 'system_modules/ui_core/static');
  app.use(express.static(distPath));

  // SPA fallback — all non-file routes serve index.html
  app.get('*', (_req, res) => {
    res.sendFile(path.join(distPath, 'index.html'));
  });

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`SelenaCore UI serving static files on http://localhost:${PORT}`);
  });
}

startServer();
