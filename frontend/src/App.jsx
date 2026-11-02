import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Ledger from './Ledger'
import Contracts from './Contracts'
import Approvals from './Approvals'
import Audit from './Audit'
import MilestoneDrawer from './MilestoneDrawer'
import Copilot from './Copilot'

const PAGES = [
  ['ledger', 'Receivables'],
  ['contracts', 'Contracts'],
  ['approvals', 'Approvals'],
  ['audit', 'Audit trail'],
]

export default function App() {
  const [page, setPage] = useState('ledger')
  const [status, setStatus] = useState(null)
  const [ledger, setLedger] = useState([])
  const [pending, setPending] = useState([])
  const [openMs, setOpenMs] = useState(null)
  const [toast, setToast] = useState(null)

  const notify = useCallback((msg, err = false) => {
    setToast({ msg, err })
    setTimeout(() => setToast(null), err ? 6000 : 3200)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [l, p] = await Promise.all([api.get('/api/ledger'), api.get('/api/actions?status=PENDING')])
      setLedger(l)
      setPending(p)
    } catch (e) {
      notify(e.message, true)
    }
  }, [notify])

  useEffect(() => {
    api.get('/api/status').then(setStatus).catch(() => {})
    refresh()
    const t = setInterval(async () => {
      try { await api.post('/api/sync') } catch { /* polling */ }
      refresh()
    }, 8000)
    return () => clearInterval(t)
  }, [refresh])

  const ctx = { ledger, pending, refresh, notify, openMilestone: setOpenMs, status, go: setPage }

  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <div className="brand">
          <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
            <circle cx="16" cy="16" r="13" fill="none" stroke="#fff" strokeWidth="3" />
            <path d="M10 16.5l4 4 8-9" fill="none" stroke="#f5b301" strokeWidth="3" strokeLinecap="round" />
          </svg>
          <div><b>ProofPay</b><small>Get paid for proven work</small></div>
        </div>
        {PAGES.map(([id, label]) => (
          <button key={id} aria-current={page === id ? 'page' : undefined} onClick={() => setPage(id)}>
            {label}
            {id === 'approvals' && pending.length > 0 && <span className="badge">{pending.length}</span>}
          </button>
        ))}
        {status && (
          <div className="foot">
            <span><i className="dot" style={{ background: status.paypal_mode === 'sandbox' ? '#3ccf8e' : '#f5b301' }} />
              PayPal {status.paypal_mode === 'sandbox' ? 'sandbox (live API)' : 'offline simulator'}</span>
            <span><i className="dot" style={{ background: status.llm_live ? '#3ccf8e' : '#f5b301' }} />
              AI: {status.llm}</span>
          </div>
        )}
      </nav>
      <main>
        {page === 'ledger' && <Ledger {...ctx} />}
        {page === 'contracts' && <Contracts {...ctx} />}
        {page === 'approvals' && <Approvals {...ctx} />}
        {page === 'audit' && <Audit {...ctx} />}
      </main>
      {openMs && <MilestoneDrawer id={openMs} onClose={() => { setOpenMs(null); refresh() }} {...ctx} />}
      <Copilot refresh={refresh} />
      {toast && <div role="status" className={`toast ${toast.err ? 'err' : ''}`}>{toast.msg}</div>}
    </div>
  )
}
