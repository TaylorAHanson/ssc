import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initDiagnostics } from './lib/diagnostics'
import { initClientEvents } from './lib/clientEvents'

// Start capturing console + network diagnostics early so bug reports can
// attach recent context. Runs before React mounts.
initDiagnostics()
initClientEvents()

createRoot(document.getElementById('root')!).render(
    <App />
)
