const express = require('express');
const path = require('path');
const fs = require('fs');
const { createProxyMiddleware } = require('http-proxy-middleware');

// ── Global safety net — prevent ANY uncaught error from killing the process ──
// EIO: i/o error (broken pipes, socket resets) must never take down the server.
process.on('uncaughtException', (err) => {
  console.error('[Proxy] Uncaught exception (survived):', err.message);
});
process.on('unhandledRejection', (reason) => {
  console.error('[Proxy] Unhandled rejection (survived):', reason);
});

const app = express();
const IS_PRODUCTION = process.env.PRODUCTION === 'true';

// Proxy /api/* to FastAPI backend (both dev + prod)
app.use(
  createProxyMiddleware({
    pathFilter: '/api',
    target: 'http://localhost:8000',
    changeOrigin: true,
    proxyTimeout: 120000,
    timeout: 120000,
    on: {
      error: (err, req, res) => {
        console.error('[Proxy] API error:', err.message);
        try {
          if (res && !res.headersSent && typeof res.status === 'function') {
            res.status(502).json({ detail: 'Backend unavailable' });
          }
        } catch (_) {}
      },
      proxyReq: (proxyReq, req) => {
        // Absorb socket errors on the outbound request so they never propagate
        proxyReq.on('error', (err) => {
          console.error('[Proxy] proxyReq socket error (suppressed):', err.message);
        });
      },
      proxyRes: (proxyRes) => {
        proxyRes.on('error', (err) => {
          console.error('[Proxy] proxyRes socket error (suppressed):', err.message);
        });
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
  app.get('/favicon.ico', (req, res) => {
    res.sendFile(path.join(assetsPath, 'rp-icon.png'));
  });
  app.get('/favicon.png', (req, res) => {
    res.sendFile(path.join(assetsPath, 'rp-icon.png'));
  });

  // Serve static assets (JS bundles, images, etc.) but NOT index.html — the
  // SPA fallback below always serves index.html so that tags get injected.
  app.use(express.static(distPath, { index: false }));

  // SPA fallback — inject favicon, OG, PWA tags + loading screen into index.html
  const PWA_TAGS = `    <link rel="icon" type="image/png" href="/rp-icon.png" />
    <link rel="shortcut icon" href="/rp-icon.png" />
    <link rel="apple-touch-icon" href="/rp-icon.png" />
    <meta name="description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://reversepicks.com/" />
    <meta property="og:title" content="ReversePicks \u2014 Elite Prop Intelligence" />
    <meta property="og:description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
    <meta property="og:image" content="https://reversepicks.com/rp-icon.png" />
    <meta property="og:site_name" content="ReversePicks" />
    <meta name="twitter:card" content="summary" />
    <meta name="twitter:image" content="https://reversepicks.com/rp-icon.png" />
    <meta name="theme-color" content="#050505" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="ReversePicks" />
    <link rel="manifest" href="/manifest.json" />`;

  const LOADING_SCREEN = `
    <div id="rp-loading-screen" style="position:fixed;top:0;left:0;right:0;bottom:0;background:#050505;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:99999;transition:opacity 0.3s ease-out">
      <div style="width:80px;height:80px;border:3px solid #39FF14;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-bottom:24px;animation:rpPulse 2s ease-in-out infinite">
        <span style="color:#39FF14;font-family:system-ui,-apple-system,sans-serif;font-size:28px;font-weight:800;letter-spacing:-1px">RP</span>
      </div>
      <div style="color:#888;font-family:system-ui,-apple-system,sans-serif;font-size:14px;letter-spacing:2px;text-transform:uppercase">Loading...</div>
    </div>
    <style>@keyframes rpPulse {0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.05);opacity:0.8}}</style>
    <script>
      (function() {
        var hide = function() {
          var el = document.getElementById('rp-loading-screen');
          if (el) { el.style.opacity='0'; setTimeout(function(){el.style.display='none'},300); }
        };
        window.addEventListener('load', hide);
        setTimeout(hide, 8000);
      })();
    </script>`;

  app.use((req, res) => {
    const indexPath = path.join(distPath, 'index.html');
    if (!fs.existsSync(indexPath)) {
      res.setHeader('Cache-Control', 'no-store');
      res.status(503).send('<html><body style="font-family:sans-serif;padding:2rem;background:#050505;color:#fff"><h2>Starting up...</h2><p>The app is initialising. Please refresh in a few seconds.</p><script>setTimeout(()=>location.reload(),5000)</script></body></html>');
      return;
    }
    try {
      let html = fs.readFileSync(indexPath, 'utf8');
      // Replace existing title with the full branded one
      html = html.replace(/<title>[^<]*<\/title>/, '<title>ReversePicks \u2014 Elite Prop Intelligence</title>');
      html = html.replace('</head>', `${PWA_TAGS}\n  </head>`);
      // Inject loading screen before the closing </body> tag
      html = html.replace('</body>', `${LOADING_SCREEN}\n</body>`);
      // index.html must never be cached — JS bundles are content-hashed so they
      // cache forever, but stale index.html means users miss fresh deployments.
      res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
      res.setHeader('Pragma', 'no-cache');
      res.setHeader('Expires', '0');
      res.setHeader('Surrogate-Control', 'no-store');
      res.setHeader('Content-Type', 'text/html');
      res.send(html);
    } catch {
      res.sendFile(indexPath);
    }
  });

  console.log('[Proxy] PRODUCTION mode — serving static files from dist/');
} else {
  // Development: serve the Expo web export if available, otherwise proxy to Metro
  const distPath = path.join(__dirname, 'dist');
  const hasDist = fs.existsSync(path.join(distPath, 'index.html'));
  if (hasDist) {
    app.use(express.static(distPath));
    app.use((req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
    console.log('[Proxy] DEVELOPMENT mode — serving static dist/');
  } else {
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
