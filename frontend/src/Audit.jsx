import { useEffect, useState } from 'react'
import { api } from './api'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [chain, setChain] = useState(null)
  const verify = () => api.get('/api/audit/verify').then(setChain).catch(() => {})
  useEffect(() => {
    const load = () => api.get('/api/audit').then(setRows).catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])
  return (
    <>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="grow">
          <h1 style={{ fontSize: 30 }}>Audit trail</h1>
          <p className="muted" style={{ margin: '6px 0 0', maxWidth: '64ch' }}>Every step by a family, an agent, a person or a merchant's rule, newest first. Each entry is hash-chained to the one before, so an edited record can't hide.</p>
        </div>
        <div className="row">
          {chain && <span className={`small ${chain.ok ? 'ok' : 'no'}`}>{chain.ok ? `Intact · ${chain.rows_checked} entries · head ${chain.head.slice(0, 12)}…` : `Broken at entry ${chain.broken_at_id}`}</span>}
          <button className="btn" onClick={verify}>Verify the chain</button>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        {rows.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nothing yet.</p> : (
          <ul className="audit">
            {rows.map((r) => (
              <li key={r.id}>
                <span className="faint num">{r.ts.replace('T', ' ').replace('+00:00', '')}</span>
                <span className="who">{r.actor.replace(/_/g, ' ')}</span>
                <span>{r.action}{r.detail && <details><summary>Details</summary><pre>{JSON.stringify(r.detail, null, 2)}</pre></details>}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
