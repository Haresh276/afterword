import { useEffect, useState } from 'react'
import { api } from './api'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [chain, setChain] = useState(null)
  const verify = () => api.get('/api/audit/verify').then(setChain).catch(() => {})
  useEffect(() => {
    const load = () => api.get('/api/audit').then(setRows).catch(() => {})
    load()
    const t = setInterval(load, 6000)
    return () => clearInterval(t)
  }, [])
  const visible = rows.filter((r) => !(r.actor === 'system' && r.action === 'paypal.get_invoice'))
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit trail</h1>
          <p className="lede">Every step taken by an agent or by you, newest first. Money-moving PayPal calls always show a human approval right before them. Each entry is hash-chained to the one before, so an edited record can't hide.</p>
        </div>
        <div className="row">
          {chain && (chain.ok
            ? <span className="small"><span className="stamp PASS">Intact</span> {chain.rows_checked} entries, head <span className="num">{chain.head.slice(0, 12)}…</span></span>
            : <span className="small"><span className="stamp FAIL">Broken</span> at entry {chain.broken_at_id}</span>)}
          <button className="btn" onClick={verify}>Verify the chain</button>
        </div>
      </div>
      <div className="panel">
        {visible.length === 0 ? <p className="muted">No activity yet.</p> : (
          <ul className="audit">
            {visible.map((r) => (
              <li key={r.id}>
                <span className="t num">{r.ts.replace('T', ' ').replace('+00:00', ' UTC')}</span>
                <span className={`who ${r.actor.startsWith('human') ? 'human' : ''}`}>{r.actor.replace(/_/g, ' ')}</span>
                <span>
                  {r.action}
                  {r.detail && (
                    <details><summary>Details</summary><pre>{JSON.stringify(r.detail, null, 2)}</pre></details>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
