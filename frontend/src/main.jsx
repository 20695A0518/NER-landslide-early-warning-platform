import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
// Leaflet first: its stylesheet sets `.leaflet-container { background: #ddd }`,
// and importing it last would override our dark map background - showing a
// light grey slab inside a dark control room whenever tiles are still
// loading or unavailable offline.
import 'leaflet/dist/leaflet.css'
import './styles/app.css'
import './styles/motion.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
