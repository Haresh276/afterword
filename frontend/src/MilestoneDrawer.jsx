import { useCallback, useEffect, useState } from 'react'
import { api, money, pct, STATUS_LABEL } from './api'
import { ApprovalCard } from './Approvals'

function ProofReport({ report, fresh }) {
  const pass = report.score >= 0.7
  return (
    <div className="panel stack">
      <div className="row">
        <div>
          <h2 style={{ margin: 0 }}>Proof check</h2>
          <div className="small muted">{report.engine ? `Reviewed by ${report.engine}` : ''}</div>
        </div>
        <span className="spacer" />
        <span className={`stamp big ${pass ? 'PASS' : 'FAIL'} ${fresh ? 'land' : ''}`}>{pass ? 'Verified' : 'Needs work'} {pct(report.score)}</span>
      </div>
      <ul className="criteria">
        {report.criteria.map((c, i) => (
          <li key={i}>
            <span>{c.criterion}</span>
            <span className={`stamp ${c.verdict} ${fresh ? 'land' : ''}`} style={fresh ? { animationDelay: `${0.12 * i}s` } : null}>{c.verdict}</span>
            <span className="ev">{c.evidence}</span>
          </li>
        ))}
      </ul>
      {report.summary && <p style={{ margin: 0 }}>{report.summary}</p>}
      {report.missing && report.missing.length > 0 && (
        <div>
          <h3>Add before billing</h3>
          <ul className="flags">{report.missing.map((m, i) => <li key={i}>{m}</li>)}</ul>
        </div>
      )}
    </div>
  )
}

export default function MilestoneDrawer({ id, onClose, notify, refresh, status }) {
  const [d, setD] = useState(null)
  const [kind, setKind] = useState('text')
  const [value, setValue] = useState('')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState(false)
  const [override, setOverride] = useState('')

  const load = useCallback(async () => {
    try { setD(await api.get(`/api/milestones/${id}`)) } catch (e) { notify(e.message, true) }
  }, [id, notify])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])

  const run = async (fn, okMsg) => {
    setBusy(true)
    try { await fn(); await load(); refresh(); if (okMsg) notify(okMsg) } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }

  if (!d) return <><div className="scrim" onClick={onClose} /><aside className="drawer" aria-label="Milestone"><p className="muted">Loading</p></aside></>
  const { milestone: m, contract: c, pending } = d
  const canSubmit = ['CONFIRMED', 'SUBMITTED', 'VERIFIED'].includes(m.status)
  const submit = () => run(async () => {
    await api.form(`/api/milestones/${m.id}/deliverable`, { kind, value, file })
    setFresh(true); setValue(''); setFile(null)
  })

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer stack" role="dialog" aria-modal="true" aria-label={`Milestone ${m.name}`}>
        <div className="row" style={{ alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <div className="small muted">{c.client_name}, {c.title}</div>
            <h1 style={{ fontSize: 26, marginTop: 2 }}>{m.seq}. {m.name}</h1>
            <div className="row small" style={{ marginTop: 6 }}>
              <strong className="num" style={{ fontSize: 17 }}>{money(m.amount, c.currency)}</strong>
              {m.due_date && <span className="muted">due {m.due_date}</span>}
              <span className={`pill ${m.status}`}>{STATUS_LABEL[m.status]}</span>
            </div>
          </div>
          <button className="x" onClick={onClose} aria-label="Close">×</button>
        </div>

        {m.status === 'PAID' && (
          <div className="panel row">
            <span className="stamp big PASS land">Paid</span>
            <div className="small">PayPal invoice <span className="num">{m.invoice_id}</span> was paid {m.paid_at ? `on ${m.paid_at.slice(0, 10)}` : ''}.</div>
          </div>
        )}

        <div className="panel">
          <h3>What "done" means for this milestone</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{(m.acceptance_criteria || []).map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>

        {m.proof_report && <ProofReport report={m.proof_report} fresh={fresh} />}

        {canSubmit && (
          <div className="panel stack">
            <h2 style={{ margin: 0 }}>{m.proof_report ? 'Submit updated work' : 'Submit your work'}</h2>
            <div className="seg" role="group" aria-label="Deliverable type">
              {[['text', 'Delivery note'], ['url', 'Link or GitHub repo'], ['file', 'File']].map(([k, l]) => (
                <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>
              ))}
            </div>
            {kind === 'text' && <textarea placeholder="Describe what you delivered and where the client can find it." value={value} onChange={(e) => setValue(e.target.value)} />}
            {kind === 'url' && <input type="url" placeholder="https://github.com/you/project or a shared folder link" value={value} onChange={(e) => setValue(e.target.value)} />}
            {kind === 'file' && <input type="file" accept=".pdf,.txt,.md,.py,.js,.json,.csv,.html" onChange={(e) => setFile(e.target.files[0] || null)} />}
            <div className="row">
              <button className="btn primary" disabled={busy || (kind === 'file' ? !file : !value.trim())} onClick={submit}>
                {busy ? 'Checking against the contract' : 'Check my work'}
              </button>
            </div>
          </div>
        )}

        {pending.map((a) => <ApprovalCard key={a.id} a={a} onDone={() => { load(); refresh() }} notify={notify} />)}

        {['VERIFIED', 'SUBMITTED'].includes(m.status) && m.proof_report && pending.length === 0 && (
          <div className="panel stack">
            {m.status === 'VERIFIED' ? (
              <>
                <h2 style={{ margin: 0 }}>Bill the client</h2>
                <p className="small muted" style={{ margin: 0 }}>ProofPay drafts the PayPal invoice and runs safety checks. You approve before anything is sent.</p>
                <div><button className="btn primary" disabled={busy} onClick={() => run(() => api.post(`/api/milestones/${m.id}/propose-invoice`, {}), 'Invoice drafted. Review and approve it below.')}>Draft PayPal invoice</button></div>
              </>
            ) : (
              <>
                <h2 style={{ margin: 0 }}>Bill anyway?</h2>
                <p className="small muted" style={{ margin: 0 }}>The proof check didn't pass. If the client already accepted the work outside ProofPay, record why and bill.</p>
                <input type="text" placeholder="e.g. Client approved on a call on 12 Nov" value={override} onChange={(e) => setOverride(e.target.value)} />
                <div><button className="btn" disabled={busy || !override.trim()} onClick={() => run(() => api.post(`/api/milestones/${m.id}/propose-invoice`, { override_reason: override }), 'Invoice drafted with your note.')}>Draft invoice with override</button></div>
              </>
            )}
          </div>
        )}

        {m.status === 'INVOICED' && (
          <div className="panel stack">
            <h2 style={{ margin: 0 }}>Waiting on payment</h2>
            <p className="small" style={{ margin: 0 }}>
              PayPal invoice <span className="num">{m.invoice_id}</span> ({m.invoice_status}), sent {m.sent_at ? m.sent_at.slice(0, 10) : ''}.
              {' '}{m.reminders_sent ? `${m.reminders_sent} reminder${m.reminders_sent > 1 ? 's' : ''} sent.` : 'No reminders yet.'}
            </p>
            <div className="row">
              {m.invoice_url && <a className="btn" href={m.invoice_url} target="_blank" rel="noreferrer">Open the client's PayPal invoice</a>}
              {pending.length === 0 && <button className="btn" disabled={busy} onClick={() => run(() => api.post(`/api/milestones/${m.id}/propose-reminder`), 'Reminder drafted. Approve it below.')}>Draft a reminder</button>}
              {status && status.paypal_mode !== 'sandbox' && (
                <button className="btn go" disabled={busy} onClick={() => run(() => api.post(`/api/mock/pay/${m.invoice_id}`), 'Payment received')}>Simulate client payment</button>
              )}
            </div>
          </div>
        )}

        {m.proof_report && (
          <div className="row">
            <a className="btn" href={`/api/milestones/${m.id}/evidence.pdf`} target="_blank" rel="noreferrer">Download evidence pack (PDF)</a>
            <span className="small muted">Contract, proof check and audit trail in one file, ready for a dispute.</span>
          </div>
        )}
      </aside>
    </>
  )
}
