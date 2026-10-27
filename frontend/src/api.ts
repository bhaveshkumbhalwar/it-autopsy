const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.error || body.detail || detail
    } catch {
      // Keep the HTTP status text when the body is not JSON.
    }
    throw new Error(detail)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => apiRequest<{ status: string }>('/api/health'),
  listCases: (query = '', status = 'all', priority = 'all') =>
    apiRequest<{ cases: any[] }>(
      `/api/cases?query=${encodeURIComponent(query)}&status=${encodeURIComponent(status === 'all' ? '' : status)}&priority=${encodeURIComponent(priority === 'all' ? '' : priority)}`,
    ),
  getCase: (id: string) => apiRequest<{ case: any }>(`/api/cases/${id}`),
  createCase: (issue: string, os: string, allowReal = false, approved = false) =>
    apiRequest<any>('/api/cases', {
      method: 'POST',
      body: JSON.stringify({ issue, os, allow_real: allowReal, approved }),
    }),
  continueCase: (id: string, message: string, os: string, allowReal = false, approved = false, forceRefresh = false) =>
    apiRequest<any>(`/api/cases/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message, os, allow_real: allowReal, approved, force_refresh: forceRefresh }),
    }),
  confirmCase: (id: string, resolved: boolean) =>
    apiRequest<any>(`/api/cases/${id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ resolved }),
    }),
  listEvents: (id: string) => apiRequest<{ events: any[] }>(`/api/cases/${id}/events`),
  tools: () => apiRequest<{ tools: any[] }>('/api/diagnostics/tools'),
  diagnosticEvents: (id: string) => apiRequest<{ events: any[] }>(`/api/diagnostics/events?case_id=${encodeURIComponent(id)}`),
  searchKnowledge: (query: string, os: string) =>
    apiRequest<{ results: any[] }>(`/api/knowledge/search?q=${encodeURIComponent(query)}&os=${encodeURIComponent(os)}`),
  operationsSummary: () => apiRequest<{ summary: any }>('/api/operations/summary'),
  operationsVolume: () => apiRequest<{ volume: any[] }>('/api/operations/volume'),
  operationsFailures: () => apiRequest<{ failures: any[] }>('/api/operations/failures'),
  evaluation: () => apiRequest<any>('/api/evaluation'),
  settings: () => apiRequest<any>('/api/settings'),
}
