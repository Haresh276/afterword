import { useEffect, useState } from 'react'
import { api, money, STATUS_LABEL } from './api'

function MilestoneEditor({ m, currency, locked, onSaved, notify }) {
  const [d, setD] = useState({ name: m.name, amount: m.amount, due_date: m.due_date || '', criteria: (m.acceptance_criteria || []).join('\n') })
  const dirty = d.name !== m.name || Number(d.amount) !== m.amount || (d.due_date || null) !== (m.due_date || null)
    || d.criteria !== (m.acceptance_criteria || []).join('\n')
  const save = async () => {
    try {
      await api.patch(`/api/milestones/${m.id}`, {
        name: d.name, amount: Number(d.amount), due_date: d.due_date || null,
        acceptance_criteria: d.criteria.split('\n').map((s) => s.trim()).filter(Boolean),
      })
      onSaved()
      notify('Milestone saved')
    } catch (e) { notify(e.message, true) }
  }
  return (
    <div className="ms-edit">
      <div className="top">
        <span className="seq">{m.seq}</span>
        <input type="text" aria-label="Milestone name" value={d.name} disabled={locked} onChange={(e) => setD({ ...d, name: e.target.value })} />
        <input type="number" aria-label={`Amount in ${currency}`} step="0.01" value={d.amount} disabled={locked} onChange={(e) => setD({ ...d, amount: e.target.value })} />
        <input type="date" aria-label="Due date" value={d.due_date} disabled={locked} onChange={(e) => setD({ ...d, due_date: e.target.value })} />
      </div>
      <label className="f" style={{ marginTop: 10 }}>Acceptance criteria, one per line. The proof check tests each line.</label>
      <textarea rows={Math.max(3, d.criteria.split('\n').length)} style={{ minHeight: 0 }} value={d.criteria} disabled={locked}
        onChange={(e) => setD({ ...d, criteria: e.target.value })} />
      <div className="row" style={{ marginTop: 8 }}>
        <span className={`pill ${m.status}`}>{STATUS_LABEL[m.status]}</span>
        <span className="spacer" />
        {!locked && dirty && <button className="btn sm primary" onClick={save}>Save milestone</button>}
      </div>
    </div>
  )
}

function Review({ contract, flags, engine, reload, notify, openMilestone }) {
  const pick = (c) => ({ client_name: c.client_name || '', client_email: c.client_email || '', freelancer_gstin: c.freelancer_gstin || '', lut_arn: c.lut_arn || '', sac_code: c.sac_code || '' })
  const [p, setP] = useState(pick(contract))
  useEffect(() => setP(pick(contract)), [contract])
  const changed = JSON.stringify(p) !== JSON.stringify(pick(contract))
  const sum = contract.milestones.reduce((s, m) => s + m.amount, 0)
  const drafts = contract.milestones.some((m) => m.status === 'DRAFT')
  const anyInvoiced = contract.milestones.some((m) => m.invoice_id)

  const saveParties = async () => {
    try { await api.patch(`/api/contracts/${contract.id}`, p); await reload(); notify('Client details saved') } catch (e) { notify(e.message, true) }
  }
  const confirm = async () => {
    try {
      if (changed) await api.patch(`/api/contracts/${contract.id}`, p)
      await api.post(`/api/contracts/${contract.id}/confirm`)
      await reload()
      notify('Milestones confirmed. Submit work on any milestone to start billing.')
    } catch (e) { notify(e.message, true) }
  }
  const remove = async () => {
    try { await api.del(`/api/contracts/${contract.id}`); await reload(null); notify('Contract removed') } catch (e) { notify(e.message, true) }
  }

  return (
    <div className="panel stack">
      <div>
        <h2>{contract.title}</h2>
        <p className="muted small" style={{ margin: 0 }}>{contract.summary}</p>
        {engine && <p className="muted small" style={{ margin: '4px 0 0' }}>Read by {engine}</p>}
      </div>
      {flags && flags.length > 0 && (
        <div>
          <h3>Before you bill, know this</h3>
          <ul className="flags">{flags.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </div>
      )}
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: 1, minWidth: 180 }}>
          <label className="f" htmlFor="cn">Client</label>
          <input id="cn" type="text" value={p.client_name} disabled={anyInvoiced} onChange={(e) => setP({ ...p, client_name: e.target.value })} />
        </div>
        <div style={{ flex: 1.3, minWidth: 220 }}>
          <label className="f" htmlFor="ce">Client's PayPal email (invoices go here)</label>
          <input id="ce" type="email" value={p.client_email} disabled={anyInvoiced} onChange={(e) => setP({ ...p, client_email: e.target.value })} />
        </div>
      </div>
      <details className="export" open={!!contract.freelancer_gstin}>
        <summary>Export details for Indian GST (printed on every invoice)</summary>
        <div className="row" style={{ alignItems: 'flex-end', marginTop: 10 }}>
          <div style={{ flex: 1.2, minWidth: 170 }}>
            <label className="f" htmlFor="gst">Your GSTIN (blank if not registered)</label>
            <input id="gst" type="text" value={p.freelancer_gstin} disabled={anyInvoiced} onChange={(e) => setP({ ...p, freelancer_gstin: e.target.value.toUpperCase() })} />
          </div>
          <div style={{ flex: 1.2, minWidth: 170 }}>
            <label className="f" htmlFor="lut">LUT ARN (zero-rated export)</label>
            <input id="lut" type="text" value={p.lut_arn} disabled={anyInvoiced || !p.freelancer_gstin} onChange={(e) => setP({ ...p, lut_arn: e.target.value.toUpperCase() })} />
          </div>
          <div style={{ flex: 0.8, minWidth: 110 }}>
            <label className="f" htmlFor="sac">SAC code</label>
            <input id="sac" type="text" value={p.sac_code} disabled={anyInvoiced} onChange={(e) => setP({ ...p, sac_code: e.target.value })} />
          </div>
        </div>
        <p className="small muted" style={{ margin: '8px 0 0' }}>SAC is suggested from the contract; confirm it with your CA. Payment must reach you in foreign currency within a year of the invoice to stay zero-rated, so ProofPay tracks that deadline.</p>
      </details>
      {!drafts && !anyInvoiced && changed && <div><button className="btn" onClick={saveParties}>Save details</button></div>}
      <div className="row">
        <h3 style={{ margin: 0 }}>Milestones</h3>
        <span className="spacer" />
        <span className={`small num ${Math.abs(sum - contract.total_amount) > 0.01 ? '' : 'muted'}`} style={Math.abs(sum - contract.total_amount) > 0.01 ? { color: 'var(--red)', fontWeight: 600 } : null}>
          {money(sum, contract.currency)} of {money(contract.total_amount, contract.currency)} contract total
        </span>
      </div>
      <div>
        {contract.milestones.map((m) => (
          <div key={m.id}>
            <MilestoneEditor m={m} currency={contract.currency} locked={!!m.invoice_id} onSaved={reload} notify={notify} />
            {m.status !== 'DRAFT' && (
              <div style={{ textAlign: 'right', margin: '6px 0 0' }}>
                <button className="btn sm" onClick={() => openMilestone(m.id)}>Open milestone</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="row">
        {drafts && <button className="btn primary" onClick={confirm}>Confirm milestones</button>}
        <span className="spacer" />
        {!anyInvoiced && <button className="btn danger sm" onClick={remove}>Delete contract</button>}
      </div>
    </div>
  )
}

export default function Contracts({ notify, refresh, openMilestone }) {
  const [text, setText] = useState('')
  const [file, setFile] = useState(null)
  const [samples, setSamples] = useState([])
  const [busy, setBusy] = useState(false)
  const [list, setList] = useState([])
  const [sel, setSel] = useState(null)

  const load = async (keep = sel) => {
    const l = await api.get('/api/contracts')
    setList(l)
    setSel(keep && l.some((c) => c.id === keep) ? keep : (l[0] && l[0].id) || null)
    refresh()
  }
  useEffect(() => {
    api.get('/api/samples').then(setSamples).catch(() => {})
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const extract = async () => {
    setBusy(true)
    try {
      const c = await api.form('/api/contracts', { text, file })
      setText(''); setFile(null)
      await load(c.id)
      notify(`Found ${c.milestones.length} milestones`)
    } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }

  const current = list.find((c) => c.id === sel)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Contracts</h1>
          <p className="lede">Paste the agreement you signed. ProofPay pulls out each milestone, its price, its deadline and what "done" means, and warns you about clauses that delay payment.</p>
        </div>
      </div>
      <div className="grid2">
        <div className="stack">
          <div className="panel stack">
            <div>
              <label className="f" htmlFor="ct">Contract or statement of work</label>
              <textarea id="ct" className="contract" placeholder="Paste the contract text here, or upload a PDF or text file below."
                value={text} onChange={(e) => setText(e.target.value)} />
            </div>
            <div className="row">
              <input type="file" accept=".pdf,.txt,.md" aria-label="Upload contract file" onChange={(e) => setFile(e.target.files[0] || null)} />
              <span className="spacer" />
              <button className="btn primary" disabled={busy || (!text.trim() && !file)} onClick={extract}>
                {busy ? 'Reading contract' : 'Read contract'}
              </button>
            </div>
            {samples.length > 0 && (
              <div className="row small">
                <span className="muted">Try a sample:</span>
                {samples.map((s) => (
                  <button key={s.name} className="btn sm" onClick={() => setText(s.text)}>
                    {s.name.replace('contract_', '').replace(/_/g, ' ')}
                  </button>
                ))}
              </div>
            )}
          </div>
          {list.length > 0 && (
            <div className="panel">
              <h3>Your contracts</h3>
              <div className="stack" style={{ marginTop: 6 }}>
                {list.map((c) => {
                  const paid = c.milestones.filter((m) => m.status === 'PAID').reduce((s, m) => s + m.amount, 0)
                  return (
                    <button key={c.id} className="btn" style={{ width: '100%', justifyContent: 'space-between', borderColor: c.id === sel ? 'var(--blue)' : undefined }}
                      onClick={() => setSel(c.id)}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.client_name || 'Unknown client'}: {c.title}</span>
                      <span className="num small muted">{money(paid, c.currency)} / {money(c.total_amount, c.currency)}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
        {current ? (
          <Review key={current.id} contract={current} flags={current.risk_flags} engine={current.engine}
            reload={load} notify={notify} openMilestone={openMilestone} />
        ) : (
          <div className="panel empty"><h2>No contract yet</h2><p>Paste one on the left, or pick a sample to see how it works.</p></div>
        )}
      </div>
    </>
  )
}
