import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtDate, LANGS, money, pct } from './api'

const RELATIONS = ['daughter', 'son', 'spouse', 'partner', 'parent', 'sibling', 'grandchild', 'executor', 'friend']
const store = {
  get: () => { try { return localStorage.getItem('afterword-case') } catch { return null } },
  set: (v) => { try { v ? localStorage.setItem('afterword-case', v) : localStorage.removeItem('afterword-case') } catch { /* private mode */ } },
}

function Step({ n, title, done, locked, children }) {
  return (
    <section className={`step ${done ? 'done' : ''} ${locked ? 'locked' : ''}`} aria-disabled={locked || undefined}>
      <div className="step-head"><span className="step-n" aria-hidden="true">{done ? '✓' : n}</span><h2>{title}</h2></div>
      <div className="step-body">{children}</div>
    </section>
  )
}

function About({ onStart, busy }) {
  const [f, setF] = useState({ claimant_name: '', claimant_email: '', relationship: 'daughter', language: 'en', deceased_email: '' })
  const demo = () => setF({ claimant_name: 'Priya Narayanan', claimant_email: 'priya.narayanan@example.com', relationship: 'daughter',
    language: 'en', deceased_email: '' })
  const ok = f.claimant_name.trim() && /.+@.+\..+/.test(f.claimant_email)
  return (
    <div className="card">
      <div className="fields">
        <div><label className="f" htmlFor="cn">Your name</label><input id="cn" type="text" value={f.claimant_name} onChange={(e) => setF({ ...f, claimant_name: e.target.value })} /></div>
        <div><label className="f" htmlFor="ce">Your email, for replies</label><input id="ce" type="email" value={f.claimant_email} onChange={(e) => setF({ ...f, claimant_email: e.target.value })} /></div>
        <div><label className="f" htmlFor="rel">They were your</label>
          <select id="rel" value={f.relationship} onChange={(e) => setF({ ...f, relationship: e.target.value })}>{RELATIONS.map((r) => <option key={r}>{r}</option>)}</select></div>
        <div><label className="f" htmlFor="lang">Write to me in</label>
          <select id="lang" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>{LANGS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="wide"><label className="f" htmlFor="de">Their PayPal email, if you know it</label>
          <input id="de" type="email" placeholder="We can also read it from their PayPal activity export" value={f.deceased_email} onChange={(e) => setF({ ...f, deceased_email: e.target.value })} /></div>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <button className="btn primary" disabled={!ok || busy} onClick={() => onStart(f)}>Continue</button>
        <button className="btn quiet" onClick={demo}>Fill in the demo family</button>
      </div>
    </div>
  )
}

function Certificate({ c, refresh, notify }) {
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState(null)
  const [edit, setEdit] = useState(null)
  const send = async (fields) => {
    setBusy(true)
    try { await api.form(`/api/cases/${c.id}/certificate`, fields); setFile(null); await refresh() } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }
  const save = async () => {
    try { await api.patch(`/api/cases/${c.id}`, edit); setEdit(null); await refresh() } catch (e) { notify(e.message, true) }
  }
  const v = c.verification
  const locked = c.status === 'NOTIFIED' || c.status === 'CLOSED'
  if (!c.certificate_sha256) {
    return (
      <div className="drop">
        <p style={{ margin: 0 }}>A photo or scan of the death certificate, in any language.</p>
        <input type="file" accept="image/*,.pdf" aria-label="Death certificate" onChange={(e) => setFile(e.target.files[0] || null)} />
        <div className="row" style={{ justifyContent: 'center', marginTop: 12 }}>
          <button className="btn primary" disabled={!file || busy} onClick={() => send({ file })}>{busy ? 'Reading' : 'Read certificate'}</button>
          <button className="btn" disabled={busy} onClick={() => send({ use_specimen: '1' })}>Use the demo specimen</button>
        </div>
        <p className="tiny faint" style={{ marginBottom: 0 }}>The demo specimen is fictional and stamped as such. <a href="/api/demo/specimen.png" target="_blank" rel="noreferrer">View it</a></p>
      </div>
    )
  }
  const fields = [['deceased_name', 'Name'], ['date_of_birth', 'Date of birth'], ['date_of_death', 'Date of death'],
    ['place_of_death', 'Place of death'], ['registration_number', 'Registration number'], ['issuing_authority', 'Issued by']]
  return (
    <>
      <div className="card">
        {edit ? (
          <>
            <div className="fields">
              {fields.map(([k, l]) => (
                <div key={k} className={k === 'issuing_authority' ? 'wide' : ''}><label className="f" htmlFor={k}>{l}</label>
                  <input id={k} type={k.startsWith('date') ? 'date' : 'text'} value={edit[k] || ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></div>
              ))}
            </div>
            <div className="row" style={{ marginTop: 14 }}><button className="btn primary" onClick={save}>Save</button><button className="btn" onClick={() => setEdit(null)}>Cancel</button></div>
          </>
        ) : (
          <>
            <p className="sum" style={{ margin: 0 }}>{c.deceased_name || 'Name not read'}{c.date_of_death && <>, who died on {fmtDate(c.date_of_death)}</>}{c.place_of_death && <> in {c.place_of_death}</>}.</p>
            <p className="small muted" style={{ margin: '4px 0 0' }}>Registration {c.registration_number || '?'} · {c.issuing_authority || '?'}{c.extraction?.engine && <> · read by {c.extraction.engine}</>}</p>
            {!locked && <button className="btn quiet sm" onClick={() => setEdit(Object.fromEntries(fields.map(([k]) => [k, c[k] || ''])))}>Correct something</button>}
          </>
        )}
        <div className="privacy" style={{ marginTop: 14 }}>
          <span aria-hidden="true">◌</span>
          <span>We read the certificate and deleted the image straight away. We kept only what's written above and a fingerprint of the file (sha256 {c.certificate_sha256.slice(0, 12)}…), so it can't be reused for someone else.</span>
        </div>
      </div>
      {v && (
        <div className="card">
          <div className="row"><h3 className="grow">Checks before anyone is told</h3><span className={v.blocking?.length || v.score < 0.8 ? 'no' : 'ok'}>{pct(v.score)}</span></div>
          <ul className="checks" style={{ marginTop: 8 }}>
            {v.checks.map((x, i) => (
              <li key={i}><span className={x.passed ? 'ok' : 'no'} aria-label={x.passed ? 'passed' : 'failed'}>{x.passed ? '✓' : '✕'}</span><span>{x.check}</span><span className="d">{x.detail}</span></li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

function Activity({ c, refresh, notify }) {
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState(null)
  const send = async (fields) => {
    setBusy(true)
    try { await api.form(`/api/cases/${c.id}/activity`, fields); await refresh() } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }
  const toggle = async (f) => { try { await api.patch(`/api/findings/${f.id}`, { selected: !f.selected }); await refresh() } catch (e) { notify(e.message, true) } }
  const locked = c.status === 'NOTIFIED' || c.status === 'CLOSED'
  if (!c.findings.length) {
    return (
      <div className="drop">
        <p style={{ margin: 0 }}>Their PayPal activity export (Activity → Statements → Download, as CSV). If you're the executor, PayPal can send it to you.</p>
        <input type="file" accept=".csv,text/csv" aria-label="PayPal activity export" onChange={(e) => setFile(e.target.files[0] || null)} />
        <div className="row" style={{ justifyContent: 'center', marginTop: 12 }}>
          <button className="btn primary" disabled={!file || busy} onClick={() => send({ file })}>{busy ? 'Reading' : 'Find what to stop'}</button>
          <button className="btn" disabled={busy} onClick={() => send({ use_sample: '1' })}>Use the demo export</button>
        </div>
        <p className="tiny faint" style={{ marginBottom: 0 }}><a href="/api/demo/sample-activity.csv">Download the demo export</a> to see the format.</p>
      </div>
    )
  }
  const stop = c.findings.filter((f) => f.kind === 'SUBSCRIPTION' || f.kind === 'INVOICE')
  const leave = c.findings.filter((f) => !(f.kind === 'SUBSCRIPTION' || f.kind === 'INVOICE'))
  const Row = ({ f, canPick }) => (
    <div className="finding">
      {canPick ? <input type="checkbox" checked={!!f.selected} disabled={locked} onChange={() => toggle(f)} aria-label={`Include ${f.merchant_name}`} /> : <span />}
      <span><strong style={{ fontWeight: 600 }}>{f.merchant_name}</strong>
        {f.status === 'NOT_ON_NETWORK' && <span className="tag off">not on Afterword yet</span>}
        {f.kind === 'INVOICE' && <span className="tag">open invoice</span>}</span>
      <span className="amt num">{f.kind === 'SUBSCRIPTION' ? <>{money(f.typical_amount)}<span className="faint small"> / {f.cadence === 'monthly' ? 'month' : f.cadence}</span></> : f.typical_amount ? money(f.typical_amount) : ''}</span>
      <span className="why">{f.reason}{f.owed_back > 0 && <> · <strong style={{ color: 'var(--amber)' }}>{money(f.owed_back)} taken after they died</strong></>}</span>
    </div>
  )
  return (
    <div className="card">
      <p className="sum" style={{ marginTop: 0 }}>We found <strong>{stop.length}</strong> things still charging or billing them, and <strong className="num">{money(c.totals.owed_back)}</strong> taken after {fmtDate(c.date_of_death)}.</p>
      <div className="group-title">We'll ask these to stop and refund</div>
      {stop.map((f) => <Row key={f.id} f={f} canPick />)}
      {leave.length > 0 && <><div className="group-title">We'll leave these alone</div>{leave.map((f) => <Row key={f.id} f={f} />)}</>}
    </div>
  )
}

const TRACK = ['Told', 'Checked', 'Stopped', 'Refunded']
function trackState(f) {
  const s = f.status
  const n = f.notice
  const pending = s === 'RECEIVED' || n?.status === 'IN_REVIEW' || n?.status === 'ACTIONED'
  return TRACK.map((label, i) => {
    if (s === 'CLOSED') return 'on'
    if (i === 0) return s === 'NOTIFIED' || pending ? 'on' : ''
    if (i === 1) return pending ? 'wait' : ''
    return ''
  })
}

function Board({ c }) {
  const told = c.findings.filter((f) => f.notice_id)
  const offline = c.findings.filter((f) => f.status === 'NOT_ON_NETWORK' && f.selected)
  const t = c.totals
  return (
    <>
      <p className="headline" aria-live="polite"><strong>{t.closed} of {t.notified}</strong> closed. <span className="num">{money(t.refunded)}</span> on its way back.</p>
      <p className="muted small" style={{ marginTop: 0 }}>{t.closed === t.notified ? `Every company has replied. ${money(t.monthly_stopped)} a month will no longer be taken.` : 'Each company checks the notice against its own PayPal records before acting.'}</p>
      <div className="card board" style={{ marginTop: 14 }}>
        {told.map((f) => {
          const st = trackState(f)
          const waitingPerson = f.notice?.status === 'IN_REVIEW' && !f.merchant?.auto_close
          return (
            <div className="m" key={f.id}>
              <span className="m-name">{f.merchant_name}</span>
              <span className="num small">{f.status === 'CLOSED' ? (f.kind === 'INVOICE' ? 'invoice cancelled' : `${money(f.refunded)} refunded`) : waitingPerson ? <span style={{ color: 'var(--amber)' }}>a person is reviewing</span> : ''}</span>
              <div className="track">{TRACK.map((label, i) => <span key={label} className={st[i]}>{f.kind === 'INVOICE' && i === 3 ? 'Settled' : label}</span>)}</div>
              {f.notice?.reply && <div className="reply">{f.notice.reply}</div>}
            </div>
          )
        })}
        {offline.map((f) => (
          <div className="m" key={f.id}>
            <span className="m-name">{f.merchant_name}</span><span className="small" style={{ color: 'var(--amber)' }}>not on Afterword: email ready below</span>
          </div>
        ))}
      </div>
    </>
  )
}

function Letter({ c, notify }) {
  const [lang, setLang] = useState(c.language || 'en')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async (l) => {
    setBusy(true)
    try { setText((await api.get(`/api/cases/${c.id}/letter?language=${l}`)).letter) } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }, [c.id, notify])
  useEffect(() => { load(lang) }, [lang, load, c.totals.closed])
  const offline = c.findings.filter((f) => f.email_draft)
  const copy = async (t) => { try { await navigator.clipboard.writeText(t); notify('Copied') } catch { notify('Copy failed; select the text instead', true) } }
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <label className="f" htmlFor="ll" style={{ margin: 0 }}>Language</label>
        <select id="ll" style={{ width: 'auto' }} value={lang} onChange={(e) => setLang(e.target.value)}>{LANGS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <span className="grow" />
        <a className="btn" href={`/api/cases/${c.id}/record.pdf`} target="_blank" rel="noreferrer">Download the record (PDF)</a>
      </div>
      <div className="letter-wrap" aria-busy={busy}>
        <div className={`letter ${lang !== 'en' ? 'indic' : ''}`} lang={lang}>{text || ' '}</div>
      </div>
      {offline.map((f) => (
        <div className="card" key={f.id} style={{ marginTop: 14 }}>
          <div className="row"><h3 className="grow">Email for {f.merchant_name}</h3><button className="btn sm" onClick={() => copy(f.email_draft)}>Copy email</button></div>
          <div className="email-draft">{f.email_draft}</div>
        </div>
      ))}
    </>
  )
}

export default function Family({ notify }) {
  const [c, setC] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  const load = useCallback(async (id) => {
    try { setC(await api.get(`/api/cases/${id}`)) } catch { store.set(null); setC(null) }
  }, [])
  const refresh = useCallback(() => c && load(c.id), [c, load])

  useEffect(() => { const id = store.get(); if (id) load(id) }, [load])
  useEffect(() => {
    clearInterval(timer.current)
    if (c && c.status === 'NOTIFIED') timer.current = setInterval(() => load(c.id), 1500)
    return () => clearInterval(timer.current)
  }, [c?.id, c?.status, load])

  const start = async (f) => {
    setBusy(true)
    try { const k = await api.post('/api/cases', f); store.set(k.id); setC(k) } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }
  const tell = async () => {
    setBusy(true)
    try { setC(await api.post(`/api/cases/${c.id}/notify`)) } catch (e) { notify(e.message, true) } finally { setBusy(false) }
  }
  const restart = async () => {
    if (!confirm('Start the demo again from the beginning?')) return
    try { await api.post('/api/demo/reset') } catch { /* ignore */ }
    store.set(null); setC(null); scrollTo(0, 0)
  }

  const verified = c && c.verification && c.verification.score >= 0.8 && !(c.verification.blocking || []).length
  const sent = c && (c.status === 'NOTIFIED' || c.status === 'CLOSED')
  const toTell = c ? c.findings.filter((f) => f.selected && f.merchant_id && !f.notice_id).length : 0
  const offline = c ? c.findings.filter((f) => f.selected && f.status === 'NOT_ON_NETWORK').length : 0

  return (
    <main className="column">
      <div className="intro">
        <h1>When someone dies, their subscriptions don't.</h1>
        <p>Tell us once. We read the certificate, find everything still charging their PayPal account, and send every company one verified notice. They stop the charges and refund what was taken after the day they died. You don't have to phone anyone.</p>
      </div>

      <Step n="1" title="About you" done={!!c}>
        {c ? <p className="muted" style={{ margin: 0 }}>{c.claimant_name}, {c.relationship}. Replies go to {c.claimant_email}.</p> : <About onStart={start} busy={busy} />}
      </Step>

      <Step n="2" title="The death certificate" done={!!c?.certificate_sha256 && verified} locked={!c}>
        {c && <Certificate c={c} refresh={refresh} notify={notify} />}
      </Step>

      <Step n="3" title="What's still charging them" done={!!c?.findings?.length} locked={!c?.certificate_sha256}>
        {c?.certificate_sha256 && <Activity c={c} refresh={refresh} notify={notify} />}
      </Step>

      <Step n="4" title="Tell them, once" done={sent} locked={!c?.findings?.length}>
        {c?.findings?.length > 0 && (sent ? <Board c={c} /> : (
          <div className="card">
            {verified ? (
              <>
                <p style={{ marginTop: 0 }}>Each company gets a notice signed by Afterword with only what it needs: name, PayPal email, date of death and the payments involved. Not the certificate.</p>
                <div className="row">
                  <button className="btn primary" disabled={busy || !toTell} onClick={tell}>{busy ? 'Sending' : `Tell ${toTell} ${toTell === 1 ? 'company' : 'companies'}`}</button>
                  {offline > 0 && <span className="small muted">{offline} {offline === 1 ? 'isn\'t' : 'aren\'t'} on Afterword yet; we'll write {offline === 1 ? 'that email' : 'those emails'} for you.</span>}
                </div>
              </>
            ) : (
              <p className="no" style={{ margin: 0, fontWeight: 500 }}>We can't send anything until the checks pass: {(c.verification?.blocking || []).join('; ') || 'verification score is too low'}.</p>
            )}
          </div>
        ))}
      </Step>

      <Step n="5" title="A letter for you to keep" done={c?.status === 'CLOSED'} locked={!sent}>
        {sent && <Letter c={c} notify={notify} />}
      </Step>

      {c && <p className="tiny faint" style={{ marginTop: 60 }}>All names, companies and documents in this demo are fictional. <button className="btn quiet sm" onClick={restart}>Start the demo again</button></p>}
    </main>
  )
}
