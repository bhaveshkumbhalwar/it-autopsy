import clsx from 'clsx'
import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react'

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger'
  className?: string
}) {
  const tones = {
    neutral: 'bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/20',
    brand: 'bg-brand-500/10 text-brand-700 dark:text-brand-300 border-brand-500/25',
    success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
    warning: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25',
    danger: 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/25',
  }
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5 whitespace-nowrap',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('card', className)}>{children}</div>
}

export function SectionTitle({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-[15px] font-semibold text-app">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted-app">{description}</p>}
      </div>
      {action}
    </div>
  )
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  loading?: boolean
}

export function Button({ variant = 'secondary', size = 'md', loading, className, children, ...rest }: ButtonProps) {
  const variants = {
    primary:
      'bg-brand-600 text-white hover:bg-brand-700 border border-transparent shadow-sm disabled:bg-brand-400',
    secondary:
      'surface text-app border border-app hover:bg-slate-500/5 disabled:opacity-60',
    ghost: 'text-muted-app hover:text-app hover:bg-slate-500/10 border border-transparent',
    danger: 'bg-rose-600 text-white hover:bg-rose-700 border border-transparent',
  }
  const sizes = { sm: 'h-8 px-2.5 text-[12px] gap-1.5', md: 'h-9 px-3.5 text-[13px] gap-2' }
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center rounded-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed',
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx('h-4 w-4 animate-spin text-muted-app', className)} />
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      {icon && <div className="mb-1 rounded-xl bg-slate-500/10 p-2.5 text-muted-app">{icon}</div>}
      <p className="text-[14px] font-medium text-app">{title}</p>
      {description && <p className="max-w-md text-[13px] text-muted-app">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function ErrorState({ title = 'Something went wrong', message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-xl border border-rose-500/25 bg-rose-500/5 p-4">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-rose-600 dark:text-rose-400">
        <AlertTriangle className="h-4 w-4" /> {title}
      </div>
      <p className="text-[13px] text-muted-app">{message}</p>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} />
}

export function Toast({
  kind,
  message,
  onClose,
}: {
  kind: 'success' | 'error' | 'info'
  message: string
  onClose?: () => void
}) {
  const icons = {
    success: <CheckCircle2 className="h-4 w-4 text-emerald-500" />,
    error: <XCircle className="h-4 w-4 text-rose-500" />,
    info: <Info className="h-4 w-4 text-brand-500" />,
  }
  return (
    <div className="animate-in fixed right-4 bottom-4 z-50 flex max-w-sm items-start gap-2.5 rounded-xl border border-app surface p-3.5 shadow-lg">
      <div className="mt-0.5">{icons[kind]}</div>
      <p className="text-[13px] text-app">{message}</p>
      {onClose && (
        <button onClick={onClose} className="ml-2 text-muted-app hover:text-app" aria-label="Dismiss">
          <XCircle className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-app">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-muted-app">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-lg border border-app surface px-3 py-2 text-[13px] text-app placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20'
