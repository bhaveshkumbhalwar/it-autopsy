import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  FileSearch,
  GitCompare,
  LayoutDashboard,
  Menu,
  Moon,
  Settings,
  Stethoscope,
  Sun,
  Upload,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import { useTheme } from '../lib/theme'
import { api } from '../lib/api'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/analyze', label: 'Analyze Agent', icon: Upload },
  { to: '/history', label: 'Analysis History', icon: FileSearch },
  { to: '/analytics', label: 'Failure Analytics', icon: BarChart3 },
  { to: '/compare', label: 'Compare', icon: GitCompare },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Layout() {
  const { resolved, toggle } = useTheme()
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState<string | null>(null)
  const location = useLocation()

  useEffect(() => setOpen(false), [location.pathname])

  useEffect(() => {
    api
      .settings()
      .then((s) => setVersion(s.app.version))
      .catch(() => setVersion(null))
  }, [])

  return (
    <div className="flex min-h-full">
      {/* Mobile drawer backdrop */}
      {open && <div className="fixed inset-0 z-30 bg-slate-900/40 lg:hidden" onClick={() => setOpen(false)} />}

      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r border-app surface transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-app px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Stethoscope className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-[13.5px] font-semibold text-app">Agent Autopsy</p>
            <p className="text-[11px] text-muted-app">Understand. Diagnose. Improve.</p>
          </div>
          <button className="ml-auto text-muted-app lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu">
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors',
                  isActive
                    ? 'bg-brand-500/10 text-brand-700 dark:text-brand-300'
                    : 'text-muted-app hover:bg-slate-500/8 hover:text-app',
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-app p-2.5">
          <div className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11.5px] text-muted-app">
            <Activity className="h-3.5 w-3.5 text-emerald-500" />
            <span>Engine v{version ?? '…'} · rules + ML</span>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-[248px]">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-app surface/95 px-4 backdrop-blur">
          <button className="text-muted-app lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-[13px] font-medium text-muted-app">AI agent failure diagnosis</span>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={toggle}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-app text-muted-app hover:text-app"
              aria-label="Toggle theme"
              title={`Switch to ${resolved === 'dark' ? 'light' : 'dark'} mode`}
            >
              {resolved === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
