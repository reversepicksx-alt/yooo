const express = require('express');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
const IS_PRODUCTION = process.env.PRODUCTION === 'true';

// Proxy /api/* to FastAPI backend (both dev + prod)
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

if (IS_PRODUCTION) {
  // Production: serve the built Expo web export as static files
  const distPath = path.join(__dirname, 'dist');
  app.use(express.static(distPath));

  // SPA fallback — all non-API routes serve index.html
  app.get('*', (req, res) => {
    res.sendFile(path.join(distPath, 'index.html'));
  });

  console.log('[Proxy] PRODUCTION mode — serving static files from dist/');
} else {
  // Development: proxy everything to Expo Metro dev server
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
}

const PORT = 5000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Proxy] Listening on port ${PORT}`);
  console.log('[Proxy] /api/*  → http://localhost:8000 (FastAPI backend)');
  if (IS_PRODUCTION) {
    console.log('[Proxy] /**     → dist/ (static build)');
  } else {
    console.log('[Proxy] /**     → http://localhost:5001 (Expo frontend)');
  }
});
