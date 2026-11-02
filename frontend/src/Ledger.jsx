import { useMemo, useRef, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { themeQuartz } from 'ag-grid-community'
import { api, money, pct, STATUS_LABEL } from './api'

const theme = themeQuartz.withParams({
  fontFamily: "'Public Sans', system-ui, sans-serif",
  headerFontWeight: 600,
  foregroundColor: '#141833',
  headerTextColor: '#545b7c',
  headerBackgroundColor: '#f5f6fa',
  borderColor: '#e1e4ee',
  rowHoverColor: '#eef2fd',
  accentColor: '#1d3fa8',
  wrapperBorderRadius: 10,
  spacing: 7,
  fontSize: 14,
})

function ProofCell({ value }) {
  if (value === null || value === undefined) return <span className="muted">-</span>
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: '100%' }}>
      <div className={`meter ${value < 0.7 ? 'low' : ''}`} style={{ width: 70 }}><i style={{ width: `${value * 100}%` }} /></div>
      <span className="num small">{pct(value)}</span>
    </div>
  )
}

export default function Ledger({ ledger, pending, openMilestone, refresh, notify, go, status }) {
  const totals = useMemo(() => {
    const t = { owed: 0, owedN: 0, paid: 0, paidN: 0, ready: 0, readyN: 0, oldest: 0 }
    ledger.forEach((r) => {
      if (r.status === 'INVOICED') { t.owed += r.amount; t.owedN++; t.oldest = Math.max(t.oldest, r.days_outstanding || 0) }
      if (r.status === 'PAID') { t.paid += r.amount; t.paidN++ }
      if (r.status === 'VERIFIED' || r.status === 'PENDING_APPROVAL') { t.ready += r.amount; t.readyN++ }
    })
    return t
  }, [ledger])

  const cols = useMemo(() => [
    { field: 'client_name', headerName: 'Client', minWidth: 150, filter: true },
    { field: 'contract_title', headerName: 'Project', flex: 1.4, minWidth: 180, filter: true,
      valueFormatter: (p) => (p.value || '') },
    { field: 'name', headerName: 'Milestone', flex: 1.2, minWidth: 170, valueGetter: (p) => (p.data.seq ? `${p.data.seq}. ${p.data.name}` : p.data.name) },
    { field: 'amount', headerName: 'Amount', width: 120, type: 'rightAligned', cellClass: 'num',
      valueFormatter: (p) => money(p.value, p.data.currency) },
    { field: 'due_date', headerName: 'Due', width: 115 },
    { field: 'proof_score', headerName: 'Proof', width: 140, cellRenderer: ProofCell },
    { field: 'status', headerName: 'Status', width: 200, filter: true,
      cellRenderer: (p) => (p.value ? <span className={`pill ${p.value}`}>{STATUS_LABEL[p.value] || p.value}</span> : null) },
    { field: 'days_outstanding', headerName: 'Days unpaid', width: 120, type: 'rightAligned',
      cellStyle: (p) => (p.value > 7 ? { color: '#b42318', fontWeight: 700 } : null) },
    { field: 'realisation_deadline', headerName: 'Forex deadline', width: 140, headerTooltip: 'GST: foreign currency must reach you within a year of the invoice',
      valueGetter: (p) => (p.data && p.data.status === 'INVOICED' ? p.data.realisation_deadline : null),
      cellStyle: (p) => (p.value && (new Date(p.value) - new Date()) / 864e5 < 60 ? { color: '#b42318', fontWeight: 700 } : null) },
    { field: 'invoice_id', headerName: 'PayPal invoice', width: 230, cellClass: 'num small' },
  ], [])
  const grid = useRef(null)
  const [quick, setQuick] = useState('')
  const pinned = useMemo(() => {
    if (!ledger.length) return []
    const sum = (f) => ledger.filter(f).reduce((s, r) => s + r.amount, 0)
    return [{ id: 'total', client_name: 'Total', contract_title: `${ledger.length} milestones`, name: `${money(sum((r) => r.status === 'PAID'))} paid`,
      amount: sum(() => true), currency: 'USD', status: null, seq: '' }]
  }, [ledger])

  const seed = async () => {
    try { await api.post('/api/demo/seed'); await refresh(); notify('Demo data loaded') } catch (e) { notify(e.message, true) }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="standing">
            {ledger.length === 0 ? 'Nothing billed yet.' : <>
              You're owed <strong className="num">{money(totals.owed)}</strong> on {totals.owedN} invoice{totals.owedN === 1 ? '' : 's'}
              {totals.owedN > 0 && (totals.oldest > 0 ? <>, the oldest {totals.oldest} day{totals.oldest === 1 ? '' : 's'} old</> : <>, all sent today</>)}.
            </>}
          </p>
        </div>
        <div className="row">
          {ledger.length === 0 && <button className="btn" onClick={seed}>Load demo data</button>}
          <button className="btn primary" onClick={() => go('contracts')}>Add a contract</button>
        </div>
      </div>

      {ledger.length > 0 && (
        <div className="strip">
          <div className="paid"><div className="v num">{money(totals.paid)}</div><div className="k">collected through PayPal</div></div>
          <div className="owed"><div className="v num">{money(totals.owed)}</div><div className="k">invoiced, waiting on the client</div></div>
          <div className="wait"><div className="v num">{money(totals.ready)}</div><div className="k">verified work ready to bill</div></div>
          {pending.length > 0 && (
            <div className="wait" style={{ cursor: 'pointer' }} onClick={() => go('approvals')}>
              <div className="v num">{pending.length}</div><div className="k">action{pending.length === 1 ? '' : 's'} waiting for your approval</div>
            </div>
          )}
        </div>
      )}

      {ledger.length === 0 ? (
        <div className="panel empty">
          <h2>Start with a contract</h2>
          <p>Paste or upload the agreement you signed with your client. ProofPay turns it into milestones you can bill through PayPal once the work is proven.</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn primary" onClick={() => go('contracts')}>Add a contract</button>
            <button className="btn" onClick={seed}>Load demo data</button>
          </div>
        </div>
      ) : (
        <>
        <div className="row" style={{ marginBottom: 10 }}>
          <input type="text" aria-label="Search the ledger" placeholder="Search clients, projects, invoice ids" value={quick}
            onChange={(e) => setQuick(e.target.value)} style={{ maxWidth: 340 }} />
          <span className="spacer" />
          <button className="btn sm" onClick={() => grid.current?.api.exportDataAsCsv({ fileName: 'proofpay-receivables.csv' })}>Export CSV</button>
        </div>
        <div className="grid-wrap">
          <AgGridReact
            ref={grid}
            quickFilterText={quick}
            pinnedBottomRowData={pinned}
            theme={theme}
            rowData={ledger}
            columnDefs={cols}
            getRowId={(p) => p.data.id}
            defaultColDef={{ sortable: true, resizable: true }}
            onRowClicked={(e) => e.data.id !== 'total' && openMilestone(e.data.id)}
            rowStyle={{ cursor: 'pointer' }}
            animateRows
          />
        </div>
        </>
      )}
      {status && status.paypal_mode !== 'sandbox' && ledger.length > 0 && (
        <p className="small muted" style={{ marginTop: 10 }}>
          Running on the offline PayPal simulator. Add sandbox credentials to send real sandbox invoices.
        </p>
      )}
    </>
  )
}
