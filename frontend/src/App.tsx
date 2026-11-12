import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import DashboardPage from './pages/DashboardPage'
import AnalyzePage from './pages/AnalyzePage'
import HistoryPage from './pages/HistoryPage'
import ExecutionDetailPage from './pages/ExecutionDetailPage'
import AnalyticsPage from './pages/AnalyticsPage'
import ComparePage from './pages/ComparePage'
import SettingsPage from './pages/SettingsPage'
import ModelQualityPage from './pages/ModelQualityPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="analyze" element={<AnalyzePage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="executions/:id" element={<ExecutionDetailPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="settings/model-quality" element={<ModelQualityPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
