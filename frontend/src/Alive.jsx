import { useEffect, useState } from 'react'
import { api, fmtDate, fmtWait, secondsUntil } from './api'

// The page behind the link in the proof-of-life email. Only the account holder's own inbox has it.
export default function Alive({ token }) {
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { api.get(`/api/alive/${token}`).then(setInfo).catch((e) => setErr(e.message)) }, [token])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [])
  const object = async () => {
    setBusy(true)
    try { setInfo(await api.post(`/api/alive/${token}`, { note })) } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  if (err) return <main className="column"><div className="card"><p className="no" style={{ margin: 0 }}>{err}</p></div></main>
  if (!info) return <main className="column" aria-busy="true" />
  const left = secondsUntil(info.deadline, now)
  return (
    <main className="column alive">
      <div className="intro">
        <h1>{info.objected_at ? 'Thank you. Everything has stopped.' : `Is this about you, ${info.name.split(' ')[0]}?`}</h1>
        {info.objected_at ? (
          <p>We told every company the report was wrong. Billing that was paused has been resumed, nothing was cancelled, and no money moved. The person who made the report can't use Afterword for you again without a person checking first.</p>
        ) : (
          <p>{info.reported_by} ({info.relationship}) reported on {fmtDate(info.reported_at)} that {info.name} has died. If you are {info.name}, tell us and it all stops.</p>
        )}
      </div>
      {!info.objected_at && (
        <div className="card">
          <p style={{ marginTop: 0 }}>So far these companies have only <strong>paused</strong> billing: {info.companies.join(', ')}.</p>
          <p className="small muted">{left > 0 ? <>Cancellations and refunds start in <strong className="num">{fmtWait(left)}</strong>. After that they can't be undone.</> : 'The waiting period has passed. Telling us now still stops anything not yet done, and flags the report.'}</p>
          <label className="f" htmlFor="note">Anything you want us to know (optional)</label>
          <textarea id="note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: I don't know who this is." />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={busy} onClick={object}>{busy ? 'Stopping' : "I'm alive. Stop this."}</button>
          </div>
          <p className="tiny faint" style={{ marginBottom: 0 }}>If you're looking after {info.name}'s affairs after their death, you don't need to do anything here.</p>
        </div>
      )}
      <p className="tiny faint" style={{ marginTop: 40 }}><a href="/">Back to Afterword</a></p>
    </main>
  )
}
