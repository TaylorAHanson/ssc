import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

// Build timestamp to bust cache
const buildTimestamp = Date.now();

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const apiBaseUrl = mode === 'production' ? '/api/v1' : 'http://localhost:8000/api/v1';

  return {
    plugins: [react()],
    base: '/',
    css: {
      postcss: {
        plugins: [
          tailwindcss(),
          autoprefixer(),
        ]
      },
    },
    define: {
      '__BUILD_TIMESTAMP__': JSON.stringify(buildTimestamp),
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBaseUrl),
    },
    build: {
    outDir: 'backend/static',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Use timestamp in chunk names to bust cache
        entryFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        chunkFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        assetFileNames: `assets/[name]-[hash]-${buildTimestamp}.[ext]`,
      },
    },
    }
  }
})
