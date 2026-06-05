import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initDiagnostics } from './lib/diagnostics'

// Start capturing console + network diagnostics early so bug reports can
// attach recent context. Runs before React mounts.
initDiagnostics()

createRoot(document.getElementById('root')!).render(
    <App />
)
