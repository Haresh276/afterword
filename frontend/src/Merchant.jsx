import { useCallback, useEffect, useMemo, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { themeQuartz } from 'ag-grid-community'
import { api, fmtDate, money, pct } from './api'
import Audit from './Audit'

const theme = themeQuartz.withParams({
  fontFamily: "'Public Sans', system-ui, sans-serif", foregroundColor: '#1d2626', headerTextColor: '#55615f',
  headerBackgroundColor: '#f4f6f5', borderColor: '#d9dfdc', rowHoverColor: '#eef1ef', accentColor: '#5a4e6e',
  wrapperBorderRadius: 8, spacing: 7, fontSize: 14, headerFontWeight: 600,
})
const STATUS = { SENT: 'Arriving', RECEIVED: 'Received', IN_REVIEW: 'Needs review', ACTIONED: 'Partly done', CLOSED: 'Closed', REJECTED: 'Rejected' }
const TOOL = { cancel_subscription: 'Cancel subscription', refund_capture: 'Refund a charge', cancel_sent_invoice: 'Cancel open invoice' }

function Notice({ id, onClose, notify, reload }) {
  const [n, setN] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => { try { setN(await api.get(`/api/merchant/notices/${id}`)) } catch (e) { notify(e.message, true) } }, [id, notify])
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t) }, [load])
  useEffect(() => { const k = (e) => e.key === 'Escape' && onClose(); addEventListener('keydown', k); return () => removeEventListener('keydown', k) }, [onClose])
  if (!n) return null
  const p = n.payload
  const pending = n.actions.filter((a) => a.status === 'PENDING')
  const blocked = pending.some((a) => a.policy_checks.some((c) => !c.passed))
  const decide = async (fn) => { setBusy(true); try { await fn(); await load(); reload() } catch (e) { notify(e.message, true) } finally { setBusy(false) } }
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Bereavement notice">
        <div className="row" style={{ alignItems: 'flex-start' }}>
          <div className="grow">
            <div className="small muted">{n.merchant.name} · notice {n.id}</div>
            <h2 style={{ marginTop: 4 }}>{p.deceased.name}</h2>
            <div className="small muted">Died {fmtDate(p.deceased.date_of_death)} · from {p.claimant.name} ({p.claimant.relationship})</div>
          </div>
          <button className="x" aria-label="Close" onClick={onClose}>×</button>
        </div>

        <div className="card" style={{ marginTop: 18 }}>
          <h3>Is this notice genuine?</h3>
          <ul className="checks" style={{ marginTop: 6 }}>
            <li><span className={n.signature_valid ? 'ok' : 'no'}>{n.signature_valid ? '✓' : '✕'}</span><span>Signed by the Afterword hub (Ed25519)</span><span className="d">{p.protocol}</span></li>
            <li><span className={p.verification.score >= 0.8 ? 'ok' : 'no'}>{p.verification.score >= 0.8 ? '✓' : '✕'}</span><span>Certificate verified: {pct(p.verification.score)} of checks</span><span className="d">{p.verification.registration_number} · {p.verification.issuing_authority} · sha256 {p.verification.certificate_sha256.slice(0, 12)}…</span></li>
          </ul>
        </div>

        {(n.review?.matches || []).map((m) => (
          <div className="card" key={m.id}>
            <h3>{m.type === 'subscription' ? `Subscription ${m.id}` : `Invoice ${m.id}`}</h3>
            {m.found ? (
              <dl className="kv" style={{ marginTop: 8 }}>
                {m.plan && <><dt>Plan</dt><dd>{m.plan}</dd></>}
                <dt>Status on PayPal</dt><dd>{m.status}</dd>
                {m.paypal_name !== undefined && <><dt>Subscriber on PayPal</dt><dd>{m.paypal_name} · {m.paypal_email}</dd></>}
                {m.to && <><dt>Invoice sent to</dt><dd>{m.to}</dd></>}
                {m.charges_after_death && <><dt>Charges after death</dt><dd>{m.charges_after_death.length ? m.charges_after_death.map((c) => `${money(c.amount)} on ${c.time.slice(0, 10)}`).join(', ') : 'none'}</dd></>}
              </dl>
            ) : <p className="no small">Not found on PayPal: {m.detail}</p>}
          </div>
        ))}

        <h3 style={{ margin: '22px 0 10px' }}>{pending.length ? 'Your agent proposes' : 'What was done'}</h3>
        {n.actions.map((a) => (
          <div className="action" key={a.id}>
            <div className="row"><strong className="grow">{TOOL[a.tool] || a.tool}{a.amount ? ` · ${money(a.amount)}` : ''}</strong><span className={`pill ${a.status === 'EXECUTED' ? 'CLOSED' : a.status === 'PENDING' ? 'IN_REVIEW' : 'REJECTED'}`}>{a.status === 'EXECUTED' ? 'Done' : a.status.toLowerCase()}</span></div>
            <div className="small muted">{a.rationale}</div>
            <div className="tool" style={{ marginTop: 6 }}>paypal.{a.tool} · payload sha256 {a.params_hash.slice(0, 12)}…{a.decided_by ? ` · approved by ${a.decided_by}` : ''}</div>
            <ul className="checks" style={{ marginTop: 6 }}>
              {a.policy_checks.map((c, i) => <li key={i}><span className={c.passed ? 'ok' : 'no'}>{c.passed ? '✓' : '✕'}</span><span>{c.check}</span><span className="d">{c.detail}</span></li>)}
            </ul>
            {a.status === 'PENDING' && (
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn sm" disabled={busy || a.policy_checks.some((c) => !c.passed)} onClick={() => decide(() => api.post(`/api/actions/${a.id}/approve`))}>Approve this</button>
                <button className="btn quiet sm" disabled={busy} onClick={() => decide(() => api.post(`/api/actions/${a.id}/reject`, { reason: 'Rejected by support' }))}>Reject</button>
              </div>
            )}
          </div>
        ))}
        {pending.length > 0 && (
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn dusk" disabled={busy || blocked} onClick={() => decide(() => api.post(`/api/merchant/notices/${id}/approve-all`))}>
              {busy ? 'Closing' : `Approve all and close${n.review?.refund_total ? ` · refund ${money(n.review.refund_total)}` : ''}`}
            </button>
            {blocked && <span className="small no">Some checks failed, so this can't be approved.</span>}
          </div>
        )}
        {n.reply && (
          <div className="card" style={{ marginTop: 18 }}>
            <h3>Sent to {p.claimant.name}</h3>
            <div className="reply" style={{ marginTop: 8 }}>{n.reply}</div>
            {n.receipt && <p className="tiny faint" style={{ margin: '10px 0 0' }}>Closure receipt signed with {n.merchant.name}'s Ed25519 key ({n.receipt.protocol}). The Afterword hub then re-read PayPal: {n.confirmation?.items?.map((i) => `${i.what} ${i.paypal_status}`).join(', ')}.</p>}
          </div>
        )}
      </aside>
    </>
  )
}

export default function Merchant({ notify }) {
  const [merchants, setMerchants] = useState([])
  const [sel, setSel] = useState('m_dailyledger')
  const [rows, setRows] = useState([])
  const [open, setOpen] = useState(null)
  const [stats, setStats] = useState(null)

  const reload = useCallback(async () => {
    try {
      setMerchants(await api.get('/api/merchants'))
      if (sel !== 'audit') setRows(await api.get(`/api/merchant/notices${sel === 'all' ? '' : `?merchant_id=${sel}`}`))
      setStats(sel.startsWith('m_') ? await api.get(`/api/merchants/${sel}/stats`) : null)
    } catch (e) { notify(e.message, true) }
  }, [sel, notify])
  useEffect(() => { reload(); const t = setInterval(reload, 2500); return () => clearInterval(t) }, [reload])

  const m = merchants.find((x) => x.id === sel)
  const setRule = async (patch) => { try { await api.patch(`/api/merchants/${m.id}`, patch); reload() } catch (e) { notify(e.message, true) } }

  const cols = useMemo(() => [
    { field: 'deceased', headerName: 'Account holder', flex: 1.2, minWidth: 160 },
    ...(sel === 'all' ? [{ field: 'merchant_name', headerName: 'Business', minWidth: 150 }] : []),
    { field: 'date_of_death', headerName: 'Date of death', width: 135 },
    { field: 'claimant', headerName: 'From', flex: 1, minWidth: 170 },
    { field: 'score', headerName: 'Verified', width: 105, valueFormatter: (p) => pct(p.value) },
    { field: 'refund_due', headerName: 'Refund due', width: 120, type: 'rightAligned', cellClass: 'num', valueFormatter: (p) => money(p.value) },
    { field: 'status', headerName: 'Status', width: 140, cellRenderer: (p) => <span className={`pill ${p.value}`}>{STATUS[p.value] || p.value}</span> },
    { field: 'pending', headerName: 'Waiting on you', width: 135, type: 'rightAligned', cellStyle: (p) => (p.value ? { color: '#5a4e6e', fontWeight: 700 } : null) },
  ], [sel])

  return (
    <div className="console">
      <nav className="side" aria-label="Businesses">
        <h3>Businesses on Afterword</h3>
        {merchants.map((x) => (
          <button key={x.id} aria-current={sel === x.id} onClick={() => setSel(x.id)}>
            <span>{x.name}<span className="rule">{x.auto_close ? `auto-close up to ${money(x.auto_refund_cap)}` : 'every case reviewed'}</span></span>
            {x.open > 0 && <span className="count">{x.open}</span>}
          </button>
        ))}
        <button aria-current={sel === 'all'} onClick={() => setSel('all')} style={{ marginTop: 8 }}>All notices</button>
        <button aria-current={sel === 'audit'} onClick={() => setSel('audit')}>Audit trail</button>
      </nav>
      <section className="main">
        {sel === 'audit' ? <Audit /> : (
          <>
            <h1 style={{ fontSize: 30 }}>{m ? m.name : 'All notices'}</h1>
            <p className="muted" style={{ margin: '6px 0 0', maxWidth: '64ch' }}>
              Bereavement notices from families, signed by Afterword. Your agent checks each one against your own PayPal records and proposes what to cancel and refund.
            </p>
            {m && (
              <div className="row card" style={{ marginTop: 16, padding: '12px 16px' }}>
                <label className="row small" style={{ gap: 8 }}>
                  <input type="checkbox" checked={!!m.auto_close} onChange={(e) => setRule({ auto_close: e.target.checked })} />
                  Close automatically when every check passes and refunds total up to
                </label>
                <input type="text" inputMode="decimal" aria-label="Auto-close refund limit in dollars" style={{ width: 90 }} defaultValue={m.auto_refund_cap}
                  key={m.id + m.auto_refund_cap} onBlur={(e) => setRule({ auto_refund_cap: Number(e.target.value) || 0 })} disabled={!m.auto_close} />
                <span className="small faint">Anything else waits for a person.</span>
              </div>
            )}
            {stats && (
              <div className="stats">
                <div><b className="num">{stats.notices}</b><span>notices received</span></div>
                <div><b className="num">{stats.median_minutes_to_close === null ? '-' : stats.median_minutes_to_close < 1 ? '<1 min' : `${stats.median_minutes_to_close} min`}</b><span>median time to close</span></div>
                <div><b className="num">{money(stats.refunded)}</b><span>refunded to families</span></div>
                <div><b className="num">{stats.open}</b><span>waiting on your team</span></div>
              </div>
            )}
            <p className="tiny faint" style={{ margin: '10px 0 0' }}>Your own support agent can read and verify these notices over MCP at <code>/mcp</code> (tools: list_notices, get_notice, verify_notice). It can't move money.</p>
            {rows.length === 0 ? <div className="card" style={{ marginTop: 16 }}><p style={{ margin: 0 }} className="muted">No notices yet. When a family sends one from "For families", it arrives here within seconds.</p></div> : (
              <div className="grid-wrap">
                <AgGridReact theme={theme} rowData={rows} columnDefs={cols} getRowId={(p) => p.data.id}
                  defaultColDef={{ sortable: true, resizable: true }} onRowClicked={(e) => setOpen(e.data.id)} rowStyle={{ cursor: 'pointer' }} />
              </div>
            )}
          </>
        )}
      </section>
      {open && <Notice id={open} onClose={() => { setOpen(null); reload() }} notify={notify} reload={reload} />}
    </div>
  )
}
