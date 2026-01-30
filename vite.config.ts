import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build timestamp to bust cache
const buildTimestamp = Date.now();

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/',
  css: {
    postcss: './postcss.config.js',
  },
  define: {
    '__BUILD_TIMESTAMP__': JSON.stringify(buildTimestamp),
  },
  build: {
    rollupOptions: {
      output: {
        // Use timestamp in chunk names to bust cache
        entryFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        chunkFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        assetFileNames: `assets/[name]-[hash]-${buildTimestamp}.[ext]`,
      },
    },
  },
})
