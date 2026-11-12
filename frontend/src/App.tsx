import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from 'antd'
import HomePage from './pages/HomePage'
import ProjectDetailPage from './pages/ProjectDetailPage'
import SettingsPage from './pages/SettingsPage'
import StudioPage from './pages/StudioPage'
import StudioJobPage from './pages/StudioJobPage'
import StudioSettingsPage from './pages/StudioSettingsPage'
import Header from './components/Header'

const { Content } = Layout

function App() {
  return (
    <Layout>
      <Header />
      <Content>
        <Routes>
          {/* AutoClip Studio: the desktop clipping workflow (opens by default) */}
          <Route path="/" element={<StudioPage />} />
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/studio/settings" element={<StudioSettingsPage />} />
          <Route path="/studio/:id" element={<StudioJobPage />} />

          {/* Classic AI project pipeline (kept for existing projects) */}
          <Route path="/projects" element={<HomePage />} />
          <Route path="/project/:id" element={<ProjectDetailPage />} />
          <Route path="/legacy-settings" element={<SettingsPage />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Content>
    </Layout>
  )
}

export default App
