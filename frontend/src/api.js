// Each visitor gets their own demo world on the shared hosted demo (own cases, own simulated PayPal data),
// so two judges never reset or block each other.
export const SESSION = (() => {
  const make = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`)
  try {
    let s = localStorage.getItem('aw_session')
    if (!s) { s = make(); localStorage.setItem('aw_session', s) }
    return s
  } catch { return make() }
})()

async function req(method, url, body) {
  const opts = { method, headers: { 'X-Afterword-Session': SESSION } }
  if (body instanceof FormData) opts.body = body
  else if (body !== undefined) {
    opts.body = JSON.stringify(body)
    opts.headers['Content-Type'] = 'application/json'
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
  form: (u, fields) => {
    const fd = new FormData()
    Object.entries(fields).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && fd.append(k, v))
    return req('POST', u, fd)
  },
}

export const money = (n, cur = 'USD') =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: cur || 'USD', maximumFractionDigits: 2 }).format(n || 0)

export const pct = (x) => (x === null || x === undefined ? '-' : `${Math.round(x * 100)}%`)

export const fmtDate = (iso) => {
  if (!iso) return ''
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
}

export const LANGS = [['en', 'English'], ['es', 'Español'], ['zh', '中文'], ['vi', 'Tiếng Việt'], ['hi', 'हिन्दी'], ['ta', 'தமிழ்'], ['te', 'తెలుగు']]
export const OFFLINE_LANGS = ['en', 'es', 'hi']
export const runtime = { live: false } // set from /api/status; languages without a template need the model
export const langLabel = (k, l) => (runtime.live || OFFLINE_LANGS.includes(k) ? l : `${l} (needs AI key)`)

export function secondsUntil(iso, now = Date.now()) {
  return iso ? Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000)) : 0
}
export function fmtWait(s) {
  if (s >= 3600) return `${Math.round(s / 3600)} h`
  if (s >= 60) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  return `${s}s`
}
