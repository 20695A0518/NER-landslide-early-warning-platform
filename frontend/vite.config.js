import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'PRAHARI - Landslide Early Warning',
        short_name: 'PRAHARI',
        description:
          'Landslide risk monitoring and offline field reporting for the North Eastern Region',
        theme_color: '#0b1220',
        background_color: '#0b1220',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            // Map tiles: cache-first with a long TTL. A field officer in a
            // valley with no signal still needs to see where they are, and
            // tiles never change.
            urlPattern: /^https:\/\/[abc]\.tile\.openstreetmap\.org\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'osm-tiles',
              expiration: { maxEntries: 3000, maxAgeSeconds: 60 * 60 * 24 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // The offline bundle: network-first so a connected client gets
            // fresh risk levels, falling back to the last good snapshot.
            urlPattern: /\/api\/v1\/sync\/bundle/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'prahari-bundle',
              networkTimeoutSeconds: 6,
              expiration: { maxEntries: 8, maxAgeSeconds: 60 * 60 * 12 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: /\/api\/v1\/(zones|roads|alerts|dashboard)/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'prahari-api',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 80, maxAgeSeconds: 60 * 60 * 6 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // Split the heavy visualisation libraries into their own chunks so a
          // change to application code does not invalidate 400 KB of cache on
          // a connection where re-downloading it costs minutes.
          react: ['react', 'react-dom', 'react-router-dom'],
          leaflet: ['leaflet', 'react-leaflet'],
          charts: ['recharts'],
        },
      },
    },
    chunkSizeWarningLimit: 320,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // The backend serves /health at the root, not under /api. Without this
      // the dev server answers with index.html and the System page fails to
      // parse it as JSON.
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
