import { useEffect, useState } from 'react'
import { api, fmtDate, money } from './api'

const STEP = { suspend_subscription: 'Pause billing', activate_subscription: 'Resume billing', cancel_subscription: 'Cancel subscription',
  refund_capture: 'Refund a charge', cancel_sent_invoice: 'Cancel invoice' }

// For businesses -> PayPal proof: a live call to the PayPal sandbox now, and the recorded full run.
export default function Proof({ status }) {
  const [ev, setEv] = useState(null)
  const [live, setLive] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.get('/api/paypal/evidence').then(setEv).catch(() => setEv({ recorded: false })) }, [])
  const check = async () => { setBusy(true); try { setLive(await api.post('/api/paypal/live-check')) } finally { setBusy(false) } }

  return (
    <>
      <h1 style={{ fontSize: 30 }}>PayPal sandbox proof</h1>
      <p className="muted" style={{ maxWidth: '72ch', margin: '6px 0 16px' }}>
        The shared demo runs on a built-in copy of the PayPal endpoints, so every visitor can try the full flow without using up real sandbox
        subscriptions. This page shows that the same code talks to the real PayPal sandbox, two ways.
      </p>

      <div className="card">
        <div className="row"><h3 className="grow" style={{ margin: 0 }}>1. Talk to PayPal now</h3>
          <button className="btn primary sm" disabled={busy || status?.paypal_keys === false} onClick={check}>{busy ? 'Calling PayPal' : 'Run live check'}</button></div>
        <p className="small muted" style={{ margin: '6px 0 0' }}>Signs in to the sandbox, then uses the PayPal Agent Toolkit to create a product, a $1 plan and a subscription
          for Eleanor Hayes, reads it back, and deactivates the plan again. Nothing is charged.</p>
        {status?.paypal_keys === false && <p className="small" style={{ color: 'var(--amber)' }}>This server has no sandbox keys, so the live check is off. The recorded run below is the proof.</p>}
        {live?.steps && (
          <table className="mini" style={{ marginTop: 12 }}>
            <thead><tr><th>Step</th><th>PayPal id</th><th>Result</th><th>Debug id</th><th>ms</th></tr></thead>
            <tbody>{live.steps.map((s, i) => (
              <tr key={i}><td>{s.ok ? '✓' : '✗'} {s.step}</td><td className="mono">{s.id || ''}</td><td>{s.ok ? s.detail : s.error}</td>
                <td className="mono">{s.debug_id || ''}</td><td className="num">{s.ms}</td></tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>2. A full run on real sandbox subscriptions</h3>
        {!ev ? <p className="muted">Loading…</p> : !ev.recorded ? (
          <p className="small muted">Not recorded on this server yet. It is produced by <code>python scripts/sandbox_run.py</code>, which subscribes a sandbox
            buyer named Eleanor Hayes to five companies, then runs Afterword on those real subscriptions and writes <code>evidence/sandbox_run.json</code>.</p>
        ) : (
          <>
            <p className="headline small" style={{ margin: '0 0 6px' }}><strong>{ev.totals.closed} of {ev.totals.notified}</strong> companies closed ·
              <span className="num"> {money(ev.totals.refunded)}</span> refunded · <strong>{ev.totals.confirmed}</strong> confirmed by re-reading PayPal</p>
            <p className="tiny faint" style={{ marginTop: 0 }}>Run {fmtDate(ev.run_at)} against {ev.environment} · {ev.paypal_calls.count} PayPal calls, {ev.paypal_calls.failed} failed ·
              audit chain intact: {String(ev.audit_chain?.ok)}</p>
            <table className="mini">
              <thead><tr><th>Company</th><th>Step</th><th>PayPal object</th><th>PayPal said</th><th>Approved by</th><th>Debug id</th></tr></thead>
              <tbody>{ev.merchants.flatMap((m) => m.steps.map((s, i) => (
                <tr key={m.merchant + i}><td>{i === 0 ? m.merchant : ''}</td><td>{STEP[s.step] || s.step}</td><td className="mono">{s.target}</td>
                  <td>{s.paypal_status || s.status}</td><td className="small">{(s.approved_by || '').replace('policy:', 'rule: ')}</td><td className="mono">{s.paypal_debug_id}</td></tr>)))}</tbody>
            </table>
          </>
        )}
      </div>
    </>
  )
}
