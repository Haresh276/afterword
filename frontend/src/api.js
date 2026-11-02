async function req(method, url, body) {
  const opts = { method }
  if (body instanceof FormData) opts.body = body
  else if (body !== undefined) {
    opts.body = JSON.stringify(body)
    opts.headers = { 'Content-Type': 'application/json' }
  }
  const r = await fetch(url, opts)
  const text = await r.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`)
  return data
}

export const api = {
  get: (u) => req('GET', u),
  post: (u, b) => req('POST', u, b),
  patch: (u, b) => req('PATCH', u, b),
  del: (u) => req('DELETE', u),
  form: (u, fields) => {
    const fd = new FormData()
    Object.entries(fields).forEach(([k, v]) => v !== undefined && v !== null && fd.append(k, v))
    return req('POST', u, fd)
  },
}

export const money = (n, cur = 'USD') =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: cur || 'USD', maximumFractionDigits: 2 }).format(n || 0)

export const STATUS_LABEL = {
  DRAFT: 'Draft',
  CONFIRMED: 'Awaiting work',
  SUBMITTED: 'Needs more proof',
  VERIFIED: 'Verified',
  PENDING_APPROVAL: 'Awaiting your approval',
  INVOICED: 'Invoiced, unpaid',
  PAID: 'Paid',
  CANCELLED: 'Cancelled',
}

export const pct = (x) => (x === null || x === undefined ? '-' : `${Math.round(x * 100)}%`)
