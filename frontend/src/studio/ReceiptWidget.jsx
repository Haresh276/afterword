import { useEffect, useState } from 'react'

// A custom AG Studio widget: the network's money as a closure receipt.
// One line per business (refunded to families, and the year of charges stopped), a total, and the
// guarantee every Afterword receipt carries. It honours the page's filters and cross-filters like
// any built-in widget, and its fields can be remapped in edit mode.

const usd = (v) => (Number(v) || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

function value(row, field, i) {
  if (!row) return undefined
  if (Array.isArray(row)) return row[i]
  for (const k of [field?.id, `${field?.aggregation}(${field?.id})`, `${field?.id}_${field?.aggregation}`]) if (k && k in row) return row[k]
  return Object.values(row)[i]
}

function Receipt({ widgetApi, dataMapping, format }) {
  const [rows, setRows] = useState(null)
  const label = dataMapping?.label?.[0]
  const amount = dataMapping?.amount?.[0]
  const second = dataMapping?.secondary?.[0]
  useEffect(() => {
    let live = true
    if (!label || !amount) { widgetApi.setDisplayState('incompleteDataMapping'); return }
    const fields = [label, amount, ...(second ? [second] : [])]
    const draw = (res) => {
      if (!live || res.replaced) return
      const out = (res.results?.rows || []).map((r) => ({ name: value(r, label, 0), a: value(r, amount, 1), b: second ? value(r, second, 2) : null }))
        .filter((r) => r.name != null).sort((x, y) => (Number(y.a) || 0) - (Number(x.a) || 0))
      setRows(out)
      widgetApi.setDisplayState(out.length ? 'displayed' : 'noData')
    }
    widgetApi.setDisplayState('loading', { prominent: !rows })
    widgetApi.getData({ fields }, { queryId: 'receipt', onDataUpdated: draw }).then(draw).catch(() => live && widgetApi.setDisplayState('noData'))
    return () => { live = false }
  }, [widgetApi, label?.id, amount?.id, second?.id])   // eslint-disable-line react-hooks/exhaustive-deps
  if (!rows) return null
  const total = rows.reduce((s, r) => s + (Number(r.a) || 0), 0)
  const total2 = rows.reduce((s, r) => s + (Number(r.b) || 0), 0)
  return (
    <div className="aw-receipt" role="table" aria-label={format?.heading || 'Closure receipt'}>
      <div className="aw-receipt-head">
        <span className="aw-receipt-title">{format?.heading || 'Closure receipt'}</span>
        <span className="aw-receipt-cols" role="row"><span>refunded</span>{second && <span>stopped / yr</span>}</span>
      </div>
      <div className="aw-receipt-lines">
        {rows.map((r) => (
          <div className="aw-receipt-line" role="row" key={String(r.name)}>
            <span className="aw-receipt-name">{String(r.name)}</span><span className="aw-receipt-dots" />
            <span className="num">{usd(r.a)}</span>{second && <span className="num muted">{usd(r.b)}</span>}
          </div>
        ))}
      </div>
      <div className="aw-receipt-line aw-receipt-total" role="row">
        <span className="aw-receipt-name">Total</span><span className="aw-receipt-dots" />
        <span className="num">{usd(total)}</span>{second && <span className="num">{usd(total2)}</span>}
      </div>
      <div className="aw-receipt-stamp">Refunded to the original payment only · $0 paid to anyone reporting</div>
    </div>
  )
}

const icon = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3 1.5h10v13l-2-1.2-1.6 1.2-1.4-1.2-1.4 1.2L5 13.3l-2 1.2z"/><path d="M5.5 5h5M5.5 7.5h5M5.5 10h3"/></svg>'

export const receiptWidget = {
  id: 'afterword-receipt',
  label: 'Closure receipt',
  icon,
  comp: Receipt,
  dataMapping: {
    label: { type: 'field', supportedRoles: ['category'], requires: { cardinality: 'many' }, required: true, aiDescription: 'One line per value, e.g. the business.' },
    amount: { type: 'field', supportedRoles: ['numeric'], requires: { per: 'dataMapping.label', cardinality: 'one' }, required: true, aiDescription: 'Money refunded per line.' },
    secondary: { type: 'field', supportedRoles: ['numeric'], requires: { per: 'dataMapping.label', cardinality: 'one' }, required: false, aiDescription: 'Optional second money column, e.g. yearly charges stopped.' },
  },
  form: () => [{ type: 'textfield', id: 'format.heading', label: 'Heading' }],
  defaultState: { format: { heading: 'Closure receipt' } },
  defaultSize: { xSpan: 8, ySpan: 18 },
  minSize: { xSpan: 6, ySpan: 10 },
  ai: { description: 'A closure-receipt style ledger: one line per category with a money amount (and an optional second amount), a total, and Afterword\'s refund guarantee.' },
}
