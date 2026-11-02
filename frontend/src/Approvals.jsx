import { useState } from 'react'
import { api, money } from './api'

const TOOL_LABEL = {
  create_invoice: 'Create and send a PayPal invoice',
  send_invoice_reminder: 'Send a PayPal payment reminder',
}

export function ApprovalCard({ a, onDone, notify }) {
  const [busy, setBusy] = useState(false)
  const failed = a.policy_checks.filter((c) => !c.passed)
  const p = a.params
  const item = p.items && p.items[0]
  const act = async (verb) => {
    setBusy(true)
    try {
      await api.post(`/api/actions/${a.id}/${verb}`, verb === 'reject' ? { reason: 'Rejected in review' } : undefined)
      notify(verb === 'approve' ? (a.tool === 'create_invoice' ? 'Invoice sent through PayPal' : 'Reminder sent through PayPal') : 'Rejected. Nothing was sent.')
      onDone()
    } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }
  return (
    <div className="approval">
      <div className="stack">
        <div>
          <div className="small muted">{a.client_name}{a.contract_title ? `, ${a.contract_title}` : ''}</div>
          <h2 style={{ margin: '2px 0 0' }}>{TOOL_LABEL[a.tool] || a.tool}</h2>
          <div className="small muted">Proposed by {a.proposed_by.replace('_', ' ')}. {a.rationale}</div>
        </div>
        {item && <div className="amount num">{money(item.unit_amount.value, item.unit_amount.currency_code)}</div>}
        <dl className="payload">
          {item && <><dt>Line item</dt><dd>{item.name}</dd></>}
          {p.primary_recipients && <><dt>Sent to</dt><dd>{p.primary_recipients[0].billing_info.email_address}</dd></>}
          {p.subject && <><dt>Subject</dt><dd>{p.subject}</dd></>}
          {p.note && <><dt>Message</dt><dd>{p.note}</dd></>}
          {p.invoice_id && <><dt>Invoice</dt><dd className="num">{p.invoice_id}</dd></>}
          <dt>PayPal Agent Toolkit call</dt><dd className="num">{a.tool}</dd>
        </dl>
      </div>
      <div className="stack">
        <h3>Safety checks</h3>
        <ul className="checks">
          {a.policy_checks.map((c, i) => (
            <li key={i}>
              <span className={c.passed ? 'ok' : 'no'} aria-label={c.passed ? 'passed' : 'failed'}>{c.passed ? '✓' : '✗'}</span>
              <span>{c.check}</span>
              <span className="d">{c.detail}</span>
            </li>
          ))}
        </ul>
        {failed.length > 0 && <p className="small" style={{ color: 'var(--red)', margin: 0 }}>Blocked: fix the failed checks before this can run.</p>}
        <div className="row">
          <button className="btn go" disabled={busy || failed.length > 0} onClick={() => act('approve')}>
            {a.tool === 'create_invoice' ? 'Approve and send invoice' : 'Approve and send reminder'}
          </button>
          <button className="btn" disabled={busy} onClick={() => act('reject')}>Reject</button>
        </div>
      </div>
    </div>
  )
}

export default function Approvals({ pending, refresh, notify }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Approvals</h1>
          <p className="lede">ProofPay's agents can draft invoices and reminders, but nothing reaches PayPal until you approve it here. Each action is checked against the contract first.</p>
        </div>
      </div>
      {pending.length === 0
        ? <div className="panel empty"><h2>Nothing waiting on you</h2><p>When a deliverable passes its proof check, the invoice shows up here for one-click approval.</p></div>
        : pending.map((a) => <ApprovalCard key={a.id} a={a} onDone={refresh} notify={notify} />)}
    </>
  )
}
