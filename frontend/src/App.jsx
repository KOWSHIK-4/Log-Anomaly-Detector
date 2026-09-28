import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import AlertDetails from './pages/AlertDetails.jsx'
import Alerts from './pages/Alerts.jsx'
import Dashboard from './pages/Dashboard.jsx'
import { useWebSocket } from './hooks/useWebSocket.js'
import { API_BASE } from './services/api.js'
import { getWebSocketUrl } from './services/websocket.js'

function NotFound() {
  const location = useLocation()
  return (
    <div className="page">
      <div className="feed-empty">
        <div className="feed-empty-icon" aria-hidden="true">
          404
        </div>
        <h3>Page not found</h3>
        <p>
          Nothing is routed at <code>{location.pathname}</code>.
        </p>
        <a className="btn" href="/dashboard">
          Go to dashboard
        </a>
      </div>
    </div>
  )
}

/** Full-screen splash used only for the very first paint. */
function Splash() {
  return (
    <div className="app">
      <div className="boot">
        <span className="spinner" />
        <p>Connecting to the anomaly detector…</p>
        <small>Checking the backend API, then falling back to demo data if needed.</small>
      </div>
    </div>
  )
}

export default function App() {
  const data = useWebSocket()

  if (data.loading && !data.metrics) return <Splash />

  return (
    <BrowserRouter>
      <div className="app">
        <Navbar
          status={data.status}
          source={data.source}
          lastEventAt={data.lastEventAt}
          alertCount={data.alerts.length}
        />

        <main className="main">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard data={data} />} />
            <Route path="/alerts" element={<Alerts data={data} />} />
            <Route path="/alerts/:id" element={<AlertDetails data={data} />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>

        <footer className="footer">
          <span>Member 3 · React dashboard &amp; frontend AWS notification integration</span>
          <span className="dot-sep">·</span>
          <span>
            API <code>{API_BASE || 'same origin (Vite proxy)'}</code>
          </span>
          <span className="dot-sep">·</span>
          <span>
            WS <code>{getWebSocketUrl()}</code>
          </span>
        </footer>
      </div>
    </BrowserRouter>
  )
}
