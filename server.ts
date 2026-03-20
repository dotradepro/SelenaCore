import express from 'express';
import { createServer as createViteServer } from 'vite';
import path from 'path';

async function startServer() {
  const app = express();
  const PORT = 3000;

  app.use(express.json());

  // --- MOCK CORE API (Simulating Python Backend) ---
  
  // Simulated hardware stats
  let systemStats = { cpuTemp: 45, ramFree: 420, diskFree: 12000 };
  
  // Simulated Plugin Manager state
  let modules = [
    { id: 'voice-core', name: 'Voice Core', type: 'SYSTEM', status: 'running', version: '1.0.0', size: '120MB' },
    { id: 'llm-engine', name: 'LLM Intent Router', type: 'SYSTEM', status: 'idle', version: '1.0.0', size: '0MB' },
    { id: 'ha-bridge', name: 'Home Assistant Bridge', type: 'IMPORT_SOURCE', status: 'running', version: '1.2.4', size: '45MB' },
    { id: 'telegram-bot', name: 'Telegram Notifier', type: 'INTEGRATION', status: 'stopped', version: '2.0.1', size: '32MB' },
  ];

  // API: Get System Stats
  app.get('/api/system/stats', (req, res) => {
    // Simulate slight fluctuations in hardware metrics
    systemStats.cpuTemp = Math.max(30, Math.min(95, systemStats.cpuTemp + (Math.random() * 4 - 2)));
    systemStats.ramFree = Math.max(50, Math.min(4096, systemStats.ramFree + (Math.random() * 20 - 10)));
    res.json(systemStats);
  });

  // API: List Modules
  app.get('/api/modules', (req, res) => {
    res.json(modules);
  });

  // API: Toggle Module (Start/Stop)
  app.post('/api/modules/:id/toggle', (req, res) => {
    const mod = modules.find(m => m.id === req.params.id);
    if (mod) {
      if (mod.type === 'SYSTEM') {
        return res.status(403).json({ error: 'Cannot toggle SYSTEM modules' });
      }
      mod.status = mod.status === 'running' ? 'stopped' : 'running';
      res.json(mod);
    } else {
      res.status(404).json({ error: 'Module not found' });
    }
  });

  // --- VITE MIDDLEWARE ---
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`SmartHome LK Core (Mock API) running on http://localhost:${PORT}`);
  });
}

startServer();
