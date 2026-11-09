import { useEffect, useMemo, useRef, useState } from 'react'
import { AgStudioAiModule, createAiHarness, studioTheme } from 'ag-studio'
import { AgStudio, AgStudioProvider, createWidgets } from 'ag-studio-react'
import { receiptWidget } from './studio/ReceiptWidget'
import { api } from './api'
import { openaiAdapter } from './studio/openai-adapter'

// For businesses -> Insights: one AG Studio report over the whole Afterword network.
// Data comes from /api/insights: live rows from this hub plus a labelled, seeded 90-day simulation.

const f = (specs) => specs.map(([id, name, format, hide]) => ({ id, name, format, ...(hide && { hide }) }))
const sum = (id) => ({ id, aggregation: 'sum' })
const avg = (id) => ({ id, aggregation: 'avg' })
const countd = (id) => ({ id, aggregation: 'countd' })
const at = (xTrack, yTrack, xSpan, ySpan) => ({ xTrack, yTrack, xSpan, ySpan })
const title = (text) => ({ title: { enabled: true, text } })
const legend = (text) => ({ ...title(text), style: { theme: { common: { legend: { enabled: true } } } } })
const kpi = (text, value, sparklineX) => ({
  type: 'value',
  dataMapping: { value: [value], ...(sparklineX && { sparklineX: [{ id: sparklineX }] }) },
  format: { title: { enabled: true, text }, caption: { enabled: true, text: '' } },
})

function buildData(d) {
  const dates = [...d.notices.map((r) => r.received), ...d.safety.map((r) => r.date)].filter(Boolean).sort()
  return {
    description:
      'Afterword is a network that tells businesses, with a signed notice, that a PayPal customer has died. Each business pauses billing at once (reversible), ' +
      'then after a 72-hour objection window cancels the subscription, refunds charges taken after the death and cancels unpaid invoices (irreversible). ' +
      'Rows with source "Simulated" are a seeded demo history; "Live" rows come from this hub. Money is USD.',
    sources: [
      {
        id: 'notices', description: 'One row per signed death notice received by a business.', data: d.notices,
        fields: f([
          ['id', 'Notice ID', 'textFormat', true], ['source', 'Source', 'textFormat'], ['merchant', 'Business', 'textFormat'],
          ['category', 'Category', 'textFormat'], ['received', 'Received', 'dateFormat'], ['status', 'Notice status', 'textFormat'],
          ['closedBy', 'Closed by', 'textFormat'], ['secondsToPause', 'Seconds until billing paused', 'decimalFormat'],
          ['hoursToClose', 'Hours to close', 'decimalFormat'], ['refunded', 'Refunded to family', 'currencyFormat'],
          ['yearlyStopped', 'Charges stopped per year', 'currencyFormat'], ['notices', 'Notices', 'integerFormat'],
          ['relationship', 'Reported by', 'textFormat'], ['language', 'Family language', 'textFormat'],
        ]),
      },
      {
        id: 'actions', description: 'Each PayPal step a business agent proposed for a notice, and what happened to it.', data: d.actions,
        fields: f([
          ['id', 'Action ID', 'textFormat', true], ['source', 'Action source', 'textFormat'], ['merchant', 'Action business', 'textFormat'],
          ['noticeId', 'Notice', 'textFormat', true], ['step', 'Step', 'textFormat'], ['reversible', 'Reversible?', 'textFormat'],
          ['status', 'Step status', 'textFormat'], ['approvedBy', 'Approved by', 'textFormat'], ['amount', 'Step amount', 'currencyFormat'],
          ['date', 'Step date', 'dateFormat'], ['actions', 'Steps', 'integerFormat'], ['irreversibleDone', 'Irreversible steps done', 'integerFormat'],
        ]),
      },
      {
        id: 'safety', description: 'Attempts to misuse Afterword that were stopped, and which layer stopped them. "Live (lab)" rows come from the Try to break it page.', data: d.safety,
        fields: f([
          ['id', 'Event ID', 'textFormat', true], ['source', 'Event source', 'textFormat'], ['date', 'Event date', 'dateFormat'],
          ['attempt', 'Attempt', 'textFormat'], ['stoppedBy', 'Stopped by', 'textFormat'], ['outcome', 'Outcome', 'textFormat'],
          ['events', 'Attempts', 'integerFormat'],
        ]),
      },
    ],
    relationships: [
      { id: 'action-notice', source: { tableId: 'actions', fieldId: 'noticeId' }, target: { tableId: 'notices', fieldId: 'id' }, type: 'many-to-one' },
      { id: 'notices-cal', source: { tableId: 'notices', fieldId: 'received' }, target: { calendarId: 'calendar' } },
      { id: 'safety-cal', source: { tableId: 'safety', fieldId: 'date' }, target: { calendarId: 'calendar' } },
    ],
    calendars: [{ id: 'calendar', label: 'Calendar', range: { from: { type: 'date', value: dates[0] || d.asOf }, to: { type: 'date', value: d.asOf } },
      fragments: ['year', 'quarter', 'month', 'week', 'day', 'dayOfWeek'] }],
  }
}

const network = {
  id: 'network',
  widgets: {
    source: { type: 'button-filter', dataMapping: { value: [{ id: 'notices.source' }] }, format: title('Source') },
    'k-notices': kpi('Notices', countd('notices.id'), 'calendar::week'),
    'k-pause': kpi('Seconds to pause', avg('notices.secondsToPause')),
    'k-refunded': kpi('Refunded', sum('notices.refunded'), 'calendar::week'),
    'k-stopped': kpi('Stopped / year', sum('notices.yearlyStopped'), 'calendar::week'),
    weekly: { type: 'column-chart-stacked', dataMapping: { categoryKey: [{ id: 'calendar::week' }], valueKey: [sum('notices.notices')], legendKey: [{ id: 'notices.status' }] },
      format: legend('Notices per week, by status') },
    close: { type: 'bar-chart-grouped', dataMapping: { categoryKey: [{ id: 'notices.merchant' }], valueKey: [avg('notices.hoursToClose')] },
      sort: [{ field: avg('notices.hoursToClose'), direction: 'desc' }], format: title('Average hours to close (72 h window + review)') },
    by: { type: 'donut-chart', dataMapping: { categoryKey: [{ id: 'notices.closedBy' }], valueKey: [sum('notices.notices')] },
      format: { ...title('Who closed it'), style: { custom: { pieDataLabel: { enabled: true }, totalLabel: { enabled: true } } } } },
    pivot: { type: 'pivot-grid', dataMapping: { rows: [{ id: 'notices.merchant' }], columns: [{ id: 'notices.status' }], values: [sum('notices.notices')] },
      format: { ...title('Notices by business and status'), style: { grandTotalRow: { enabled: true } } } },
    receipt: { type: 'afterword-receipt', dataMapping: { label: [{ id: 'notices.merchant' }], amount: [sum('notices.refunded')], secondary: [sum('notices.yearlyStopped')] },
      format: { heading: 'Closure receipt' } },
  },
  widgetLayout: {
    source: at(0, 0, 24, 7),
    'k-notices': at(0, 7, 6, 7), 'k-pause': at(6, 7, 6, 7), 'k-refunded': at(12, 7, 6, 7), 'k-stopped': at(18, 7, 6, 7),
    weekly: at(0, 14, 10, 17), by: at(10, 14, 6, 17), receipt: at(16, 14, 8, 26), close: at(0, 31, 8, 18), pivot: at(8, 31, 8, 18),
  },
}

const steps = {
  id: 'steps',
  widgets: {
    'k-steps': kpi('Steps proposed', sum('actions.actions')),
    'k-irrev': kpi('Irreversible done', sum('actions.irreversibleDone')),
    'k-money': kpi('Refund value', sum('actions.amount')),
    mix: { type: 'column-chart-stacked', dataMapping: { categoryKey: [{ id: 'actions.step' }], valueKey: [sum('actions.actions')], legendKey: [{ id: 'actions.status' }] },
      format: legend('Every PayPal step, and what happened to it') },
    approver: { type: 'donut-chart', dataMapping: { categoryKey: [{ id: 'actions.approvedBy' }], valueKey: [sum('actions.actions')] },
      format: { ...title('Approved by'), style: { custom: { pieDataLabel: { enabled: true } } } } },
    tree: { type: 'treemap-chart', dataMapping: { categoryKey: [{ id: 'actions.merchant' }, { id: 'actions.step' }], valueKey: [sum('actions.amount')] },
      format: title('Refund value by business') },
  },
  widgetLayout: { 'k-steps': at(0, 0, 8, 7), 'k-irrev': at(8, 0, 8, 7), 'k-money': at(16, 0, 8, 7), mix: at(0, 7, 16, 18), approver: at(16, 7, 8, 18), tree: at(0, 25, 24, 16) },
}

const safety = {
  id: 'safety',
  widgets: {
    'k-blocked': kpi('Attempts stopped', sum('safety.events'), 'calendar::week'),
    layers: { type: 'sunburst-chart', dataMapping: { categoryKey: [{ id: 'safety.stoppedBy' }, { id: 'safety.attempt' }], valueKey: [sum('safety.events')] },
      format: title('Which layer stopped what') },
    list: { type: 'grid', dataMapping: { cols: [{ id: 'safety.date' }, { id: 'safety.source' }, { id: 'safety.attempt' }, { id: 'safety.stoppedBy' }, { id: 'safety.outcome' }] },
      sort: [{ field: { id: 'safety.date' }, direction: 'desc' }], format: title('Every stopped attempt') },
    who: { type: 'donut-chart', dataMapping: { categoryKey: [{ id: 'notices.relationship' }], valueKey: [sum('notices.notices')] }, format: title('Who reports a death') },
  },
  widgetLayout: { 'k-blocked': at(0, 0, 8, 7), who: at(0, 7, 8, 16), layers: at(8, 0, 16, 23), list: at(0, 23, 24, 20) },
}

// Studio's built-in widgets plus Afterword's own, in its own menu group so analysts can add it in edit mode.
const widgets = (cfg) => createWidgets({
  additionalTypes: [receiptWidget],
  menu: [...cfg.menu, { label: 'Afterword', widgetIds: ['afterword-receipt'] }],
})

const PAGES = [['network', 'Network'], ['steps', 'PayPal steps'], ['safety', 'Safety']]
const report = { selectedPageId: 'network', pages: [network, steps, safety], panels: { filters: { collapsed: true } } }

const theme = studioTheme.withParams({
  fontFamily: "'Public Sans', system-ui, sans-serif", fontSize: 14, textColor: '#1d2626', foregroundColor: '#1d2626', subtleTextColor: '#55615f',
  backgroundColor: '#ffffff', borderColor: '#d9dfdc', accentColor: '#5a4e6e', rowHoverColor: '#eef1ef',
  studioWrapperBorder: false, studioWrapperBackgroundColor: '#eef1ef', studioCanvasBackgroundColor: '#eef1ef', studioCanvasBorder: false,
  studioCanvasShadow: false, studioCanvasPadding: 12, studioWidgetBorderRadius: 8, studioWidgetTitleFontWeight: 600, studioWidgetTitleTextColor: '#1d2626',
  chartFontFamily: "'Public Sans', system-ui, sans-serif",
  chartPaletteFills1Color: '#2f5d52', chartPaletteFills2Color: '#5a4e6e', chartPaletteFills3Color: '#8f621a', chartPaletteFills4Color: '#3a6f8f',
  chartPaletteFills5Color: '#973a3a', chartPaletteFills6Color: '#7a8f5a', chartPaletteFills7Color: '#8b9593',
})

const STARTERS = [
  { label: 'Summarise this page', prompt: 'Summarise what this page shows and what stands out. Keep it to five bullet points.' },
  { label: 'Slowest businesses', prompt: 'Which businesses take longest to close a notice after the 72-hour window, and why might that be?' },
  { label: 'Add a chart', prompt: 'Add a line chart of refunds to families per week.' },
]

export default function Insights({ status }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [page, setPage] = useState('network')
  const [mode, setMode] = useState('view')
  const apiRef = useRef(null)
  const go = (id) => { const a = apiRef.current; if (a) a.setState({ ...a.getState(), selectedPageId: id }); setPage(id) }
  useEffect(() => { api.get('/api/insights').then(setD).catch((e) => setErr(e.message)) }, [])
  const data = useMemo(() => (d ? buildData(d) : null), [d])
  const ai = useMemo(() => {
    if (!status?.llm_live) return undefined
    const adapter = openaiAdapter({ endpoint: '/api/ai', model: status.llm })
    return ({ api: studio }) => createAiHarness(studio, { adapter, promptStarters: STARTERS })
  }, [status?.llm_live, status?.llm])

  if (err) return <p className="no">{err}</p>
  if (!data) return <p className="muted">Loading the network report…</p>
  return (
    <>
      <h1 style={{ fontSize: 30 }}>Network insights</h1>
      <p className="muted" style={{ margin: '6px 0 12px', maxWidth: '72ch' }}>
        Every notice, PayPal step and stopped attempt across Afterword, built with AG Studio. Use <strong>Source</strong> to switch between this hub's
        live data ({d.counts.notices.live} notices) and a seeded 90-day simulation ({d.counts.notices.simulated}) that follows the production rules.
        {ai ? ' Ask the AI assistant to explain a chart or build a new one.' : ' Add a GEMINI_API_KEY on the server to turn on the AI assistant.'}
      </p>
      <div className="row studio-bar" role="toolbar" aria-label="Report">
        <div className="world" role="group" aria-label="Report page">
          {PAGES.map(([id, label]) => <button key={id} aria-pressed={page === id} onClick={() => go(id)}>{label}</button>)}
        </div>
        <span className="grow" />
        <button className="btn sm" aria-pressed={mode === 'edit'} onClick={() => setMode(mode === 'edit' ? 'view' : 'edit')}>{mode === 'edit' ? 'Done editing' : 'Edit this report'}</button>
      </div>
      <div className="studio-wrap">
        <AgStudioProvider modules={ai ? [AgStudioAiModule] : undefined}>
          <AgStudio style={{ height: '100%', width: '100%' }} theme={theme} layout={{ widgetBorderEnabled: true }} data={data} ai={ai} widgets={widgets}
            initialState={report} mode={mode} onApiReady={(e) => { apiRef.current = e.api }} />
        </AgStudioProvider>
      </div>
    </>
  )
}
