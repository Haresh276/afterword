import { useState } from 'react'

// A short guided path for judges who land on the hosted demo without reading the README.
const KEY = 'aw_tour_closed'
const read = () => { try { return localStorage.getItem(KEY) === '1' } catch { return false } }

export default function Tour({ go }) {
  const [closed, setClosed] = useState(read)
  if (closed) return null
  const close = () => { try { localStorage.setItem(KEY, '1') } catch { /* private mode */ } setClosed(true) }
  return (
    <aside className="tour" aria-label="Three-minute tour">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="grow">
          <strong>Judging? Here's the 3-minute tour.</strong> Everything you do happens in your own copy of the demo: it never affects anyone else.
          <ol>
            <li><button className="linkish" onClick={() => go('family')}>For families</button>: <i>Fill in the demo family</i> → <i>Use the demo specimen</i> → <i>Use the demo export</i> → <i>Tell 7 companies</i>. Watch billing pause, then close.</li>
            <li><button className="linkish" onClick={() => go('merchant')}>For businesses</button>: open <i>The Daily Ledger</i>, then <i>Network insights</i> (AG Studio) and <i>PayPal sandbox proof</i>.</li>
            <li><button className="linkish" onClick={() => go('lab')}>Try to break it</button>: <i>Run every attack</i>.</li>
          </ol>
          <span className="tiny faint">Bonus: after sending, open <i>See the email</i> → the link → <i>I'm alive. Stop this.</i></span>
        </div>
        <button className="btn quiet sm" onClick={close} aria-label="Close the tour">Close</button>
      </div>
    </aside>
  )
}
