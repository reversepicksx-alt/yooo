const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();

// Proxy /api/* to FastAPI backend (preserves full path)
app.use(
  createProxyMiddleware({
    pathFilter: '/api',
    target: 'http://localhost:8000',
    changeOrigin: true,
    on: {
      error: (err, req, res) => {
        console.error('[Proxy] API error:', err.message);
        if (res && typeof res.status === 'function') {
          res.status(502).json({ detail: 'Backend unavailable' });
        }
      },
    },
  })
);

// Proxy everything else to Expo Metro dev server (with WS for hot reload)
app.use(
  createProxyMiddleware({
    pathFilter: '/**',
    target: 'http://localhost:5001',
    changeOrigin: true,
    ws: true,
    on: {
      error: (err, req, res) => {
        console.error('[Proxy] Frontend error:', err.message);
        if (res && typeof res.status === 'function') {
          res.status(502).send('Frontend loading…');
        }
      },
    },
  })
);

const PORT = 5000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Proxy] Listening on port ${PORT}`);
  console.log('[Proxy] /api/*  → http://localhost:8000 (FastAPI backend)');
  console.log('[Proxy] /**     → http://localhost:5001 (Expo frontend)');
});
