import { useEffect, useState } from 'react'
import { api } from './api'

// "Try to break it": every button runs a real attack against the real code, live.
export default function Lab({ notify }) {
  const [list, setList] = useState([])
  const [mode, setMode] = useState('')
  const [res, setRes] = useState({})
  const [running, setRunning] = useState(null)
  useEffect(() => { api.get('/api/lab').then((d) => { setList(d.scenarios); setMode(d.paypal_mode) }).catch((e) => notify(e.message, true)) }, [notify])

  const run = async (id) => {
    setRunning(id)
    try { const r = await api.post(`/api/lab/${id}`); setRes((x) => ({ ...x, [id]: r })) } catch (e) { notify(e.message, true) } finally { setRunning(null) }
  }
  const runAll = async () => { for (const s of list) await run(s.id) }
  const done = Object.values(res)
  const blocked = done.filter((r) => r.blocked).length

  return (
    <main className="column wide">
      <div className="intro">
        <h1>Try to break it.</h1>
        <p>Afterword can cancel subscriptions and move refunds, so it has to hold up against people who want to abuse it. Each attack below runs live against the same code the product uses. Nothing is special-cased for this page.</p>
      </div>
      <div className="row" style={{ marginBottom: 16 }}>
        <button className="btn primary" disabled={!!running} onClick={runAll}>{running ? 'Running' : 'Run every attack'}</button>
        {done.length > 0 && <span className="headline small" style={{ margin: 0 }}><strong>{blocked} of {done.length}</strong> blocked</span>}
        <span className="grow" />
        <span className="tiny faint">PayPal: {mode === 'sandbox' ? 'sandbox (money attacks run against the simulator so no sandbox charge is touched)' : 'simulator'}</span>
      </div>
      <div className="lab">
        {list.map((s) => {
          const r = res[s.id]
          return (
            <article className={`card attack ${r ? (r.blocked ? 'held' : 'broke') : ''}`} key={s.id} aria-busy={running === s.id}>
              <div className="row" style={{ alignItems: 'flex-start' }}>
                <div className="grow">
                  <h3 style={{ margin: 0 }}>{s.title}</h3>
                  <p className="small muted" style={{ margin: '4px 0 0' }}>{s.attack}</p>
                </div>
                {r ? <span className={`stamp ${r.blocked ? 'held' : 'broke'}`}>{r.blocked ? 'Blocked' : 'Not blocked'}</span>
                  : <button className="btn sm" disabled={!!running} onClick={() => run(s.id)}>{running === s.id ? 'Running' : 'Attack'}</button>}
              </div>
              {r && (
                <>
                  <div className="tool" style={{ marginTop: 10 }}>stopped by: {r.blocked_by} · {r.ms} ms</div>
                  <ul className="evidence">{r.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
                  <button className="btn quiet sm" disabled={!!running} onClick={() => run(s.id)}>Run again</button>
                </>
              )}
            </article>
          )
        })}
      </div>
      <p className="tiny faint" style={{ marginTop: 30 }}>Every attempt is written to the audit trail. Cases made here are marked as lab cases, so they never affect a real family's checks.</p>
    </main>
  )
}
