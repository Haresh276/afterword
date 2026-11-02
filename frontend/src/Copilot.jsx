import { useRef, useState } from 'react'
import { api } from './api'

const STARTERS = ['Who owes me the most right now?', 'Which invoice should I chase first?', 'Draft a reminder for my oldest unpaid invoice']

export default function Copilot({ refresh }) {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)

  const ask = async (text) => {
    if (!text.trim()) return
    setMsgs((m) => [...m, { me: true, text }])
    setQ('')
    setBusy(true)
    try {
      const r = await api.post('/api/copilot', { message: text })
      setMsgs((m) => [...m, { text: r.reply, tools: r.tools, engine: r.engine }])
      refresh()
    } catch (e) {
      setMsgs((m) => [...m, { text: e.message }])
    } finally {
      setBusy(false)
      setTimeout(() => end.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    }
  }

  return (
    <>
      <button className="btn primary copilot-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? 'Close Copilot' : 'Ask Copilot'}
      </button>
      {open && (
        <section className="copilot" aria-label="ProofPay Copilot">
          <div className="log">
            {msgs.length === 0 && (
              <div className="stack small">
                <p className="muted" style={{ margin: 0 }}>Ask about your invoices. Copilot reads PayPal and can draft reminders for your approval.</p>
                {STARTERS.map((s) => <button key={s} className="btn sm" style={{ whiteSpace: 'normal', textAlign: 'left' }} onClick={() => ask(s)}>{s}</button>)}
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={m.me ? 'me' : 'ai'}>
                {m.text}
                {m.tools && m.tools.length > 0 && <div className="tools">Used: {m.tools.join(', ')}</div>}
              </div>
            ))}
            {busy && <div className="ai muted">Looking at your invoices</div>}
            <span ref={end} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); ask(q) }}>
            <input type="text" aria-label="Message Copilot" placeholder="Ask about your receivables" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn primary" disabled={busy || !q.trim()}>Send</button>
          </form>
        </section>
      )}
    </>
  )
}
