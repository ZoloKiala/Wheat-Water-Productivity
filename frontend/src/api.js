/* Client for the WWP backend. Base URL is relative so the dashboard works
   from any sub-path of the EIAR site; the Vite dev server proxies /api. */

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

class ApiError extends Error {}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(BASE + path, options)
  } catch {
    throw new ApiError('Cannot reach the analysis service. Check your connection and try again.')
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = body.detail[0].msg
    } catch { /* keep the generic message */ }
    throw new ApiError(detail)
  }
  return res.json()
}

export const getAdminUnits = () => request('/admin-units')

export const getModelInfo = () => request('/model/info')

export function runAnalysis(payload, idempotencyKey) {
  return request('/analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'idempotency-key': idempotencyKey },
    body: JSON.stringify(payload),
  })
}

export function uploadBoundary(file) {
  const form = new FormData()
  form.append('file', file)
  return request('/upload', { method: 'POST', body: form })
}

const qs = (o) => new URLSearchParams(o).toString()

export const predictPoint = (p) => request('/predict?' + qs(p))
export const explainPoint = (p) => request('/explain?' + qs(p))
export const csvUrl = (runId) => `${BASE}/export/csv?run_id=${encodeURIComponent(runId)}`

export { ApiError }
