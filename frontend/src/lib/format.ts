import type { Severity } from './api'

export const CATEGORY_LABELS: Record<string, string> = {
  reasoning_failure: 'Reasoning failure',
  hallucination: 'Hallucination',
  tool_execution_failure: 'Tool execution failure',
  api_failure: 'API failure',
  memory_context_failure: 'Memory / context failure',
  planning_failure: 'Planning failure',
  infinite_loop: 'Infinite loop',
  incorrect_output: 'Incorrect output',
  safety_policy_violation: 'Safety / policy violation',
  timeout_failure: 'Timeout failure',
}

export function categoryLabel(value: string | null | undefined): string {
  if (!value) return 'None'
  return CATEGORY_LABELS[value] ?? value.replaceAll('_', ' ')
}

export const SEVERITY_STYLES: Record<Severity, string> = {
  NONE: 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  LOW: 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  MODERATE: 'bg-amber-500/12 text-amber-700 dark:text-amber-400 border-amber-500/25',
  HIGH: 'bg-orange-500/12 text-orange-700 dark:text-orange-400 border-orange-500/25',
  CRITICAL: 'bg-rose-500/12 text-rose-700 dark:text-rose-400 border-rose-500/25',
}

export const STATUS_STYLES: Record<string, string> = {
  success: 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  failed: 'bg-rose-500/12 text-rose-700 dark:text-rose-400 border-rose-500/25',
  partial: 'bg-amber-500/12 text-amber-700 dark:text-amber-400 border-amber-500/25',
  recovered: 'bg-sky-500/12 text-sky-700 dark:text-sky-400 border-sky-500/25',
  unknown: 'bg-slate-500/12 text-slate-600 dark:text-slate-300 border-slate-500/25',
}

export const SEVERITY_COLORS: Record<Severity, string> = {
  NONE: '#10b981',
  LOW: '#10b981',
  MODERATE: '#f59e0b',
  HIGH: '#f97316',
  CRITICAL: '#f43f5e',
}

export const CATEGORY_COLORS = ['#3567f0', '#8b5cf6', '#f43f5e', '#f59e0b', '#10b981', '#06b6d4', '#ec4899', '#84cc16', '#6366f1', '#f97316']

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  const diff = Date.now() - date.getTime()
  const minutes = Math.round(diff / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return formatDateTime(value)
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}
