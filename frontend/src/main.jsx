import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { api } from './api'
import { setStatusLabels } from './utils/statusLabels'

function render() {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

// Admin-configured status labels; fall back to the defaults if the
// backend can't be reached rather than blocking the page.
api
  .meta()
  .then((meta) => setStatusLabels(meta.status_labels))
  .catch(() => {})
  .finally(render)
