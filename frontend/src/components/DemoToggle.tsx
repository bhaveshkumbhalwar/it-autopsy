import { useIncludeDemo } from '../lib/demo'

/** A small switch: include demo/sample runs in the numbers or not. */
export function DemoToggle({ className = '' }: { className?: string }) {
  const [includeDemo, setIncludeDemo] = useIncludeDemo()
  return (
    <label
      className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-[11.5px] font-medium text-muted-app select-none ${className}`}
      title="Demo data is excluded from totals, charts and analytics unless this is on."
    >
      <input
        type="checkbox"
        className="h-3.5 w-3.5 accent-brand-500"
        checked={includeDemo}
        onChange={(e) => setIncludeDemo(e.target.checked)}
      />
      Include demo data
    </label>
  )
}
