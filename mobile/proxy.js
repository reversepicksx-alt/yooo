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
  const assetsPath = path.join(__dirname, 'assets');

  // PWA manifest and icon — served from stable assets folder (survives rebuilds)
  app.get('/manifest.json', (req, res) => {
    res.json({
      name: 'ReversePicks',
      short_name: 'ReversePicks',
      description: 'Elite Prop Intelligence',
      start_url: '/',
      display: 'standalone',
      background_color: '#050505',
      theme_color: '#050505',
      icons: [
        { src: '/rp-icon.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
        { src: '/rp-icon.png', sizes: '192x192', type: 'image/png' }
      ]
    });
  });
  app.get('/rp-icon.png', (req, res) => {
    res.sendFile(path.join(assetsPath, 'rp-icon.png'));
  });

  app.use(express.static(distPath));

  // SPA fallback — inject PWA tags into index.html at serve-time
  const fs = require('fs');
  const PWA_TAGS = `    <meta name="theme-color" content="#050505" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="ReversePicks" />
    <link rel="apple-touch-icon" href="/rp-icon.png" />
    <link rel="manifest" href="/manifest.json" />`;
  app.use((req, res) => {
    try {
      let html = fs.readFileSync(path.join(distPath, 'index.html'), 'utf8');
      html = html.replace('</head>', `${PWA_TAGS}\n  </head>`);
      res.setHeader('Content-Type', 'text/html');
      res.send(html);
    } catch {
      res.sendFile(path.join(distPath, 'index.html'));
    }
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
