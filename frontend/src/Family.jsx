import { useCallback, useEffect, useRef, useState } from 'react'
import { lazy, Suspense } from 'react'
import { api, fmtDate, fmtWait, langLabel, LANGS, money, OFFLINE_LANGS, pct, runtime, secondsUntil, SESSION } from './api'
const Timeline = lazy(() => import('./Timeline'))

const daysTo = (iso) => Math.max(0, Math.round((new Date(iso + 'T00:00:00') - new Date(new Date().toDateString())) / 864e5))
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
  const demo = () => setF({ claimant_name: 'Rachel Hayes', claimant_email: 'rachel.hayes@example.com', relationship: 'daughter',
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
          <select id="lang" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>{LANGS.map(([k, l]) => <option key={k} value={k} disabled={!runtime.live && !OFFLINE_LANGS.includes(k)}>{langLabel(k, l)}</option>)}</select></div>
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
        <p className="tiny faint" style={{ marginBottom: 0 }}>The demo specimen is fictional and stamped as such. <a href="/api/demo/specimen.png" target="_blank" rel="noreferrer">View it</a>
          {' · '}<button className="btn quiet sm" disabled={busy} onClick={() => send({ use_specimen: 'forged' })}>Try an edited copy (fraud demo)</button></p>
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
              <li key={i}><span className={x.passed ? 'ok' : 'no'} aria-label={x.passed ? 'passed' : 'failed'}>{x.passed ? '✓' : '✕'}</span><span>{x.check}{x.critical && !x.passed && <span className="tag off">blocks sending</span>}</span><span className="d">{x.detail}</span></li>
            ))}
          </ul>
          <Forensics fx={c.extraction?.forensics} />
          {v.network?.links?.length > 0 && (
            <div className="privacy" style={{ marginTop: 12 }}>
              <span aria-hidden="true">↔</span>
              <span>{v.network.links.map((l) => l.detail).join('; ')}. We've linked the two cases so no company is told twice.</span>
            </div>
          )}
        </div>
      )}
    </>
  )
}

function Forensics({ fx }) {
  if (!fx || !fx.ok) return null
  const e = fx.ela || {}
  return (
    <details className="fx">
      <summary>How we checked the image</summary>
      <div className="fx-body">
        {e.applicable && e.grid?.length ? (
          <div className="fx-grid" role="img" aria-label={e.anomalous ? 'Heat map: a few lines of text re-compress differently from the rest' : 'Heat map: compression is even across the page'}
            style={{ gridTemplateColumns: `repeat(${e.grid[0].length}, 1fr)` }}>
            {e.grid.flat().map((v, i) => <span key={i} style={{ background: v > 0.55 && e.anomalous ? `rgba(151,58,58,${v})` : `rgba(47,93,82,${v * 0.35})` }} />)}
          </div>
        ) : null}
        <dl className="kv small">
          <dt>Perceptual hash</dt><dd className="num">{fx.phash} <span className="faint">survives re-saving and cropping, so an edited copy of a used certificate is recognised</span></dd>
          <dt>Error-level analysis</dt><dd>{e.applicable ? `JPEG ghost sweep, best at quality ${e.quality}; peak z-score ${e.peak_z}` : e.reason}</dd>
          <dt>File metadata</dt><dd>{(fx.metadata?.editors || []).length ? `edited with ${fx.metadata.editors.join(', ')}` : 'no editing software recorded'}</dd>
        </dl>
        <p className="tiny faint" style={{ margin: 0 }}>The heat map keeps only block scores, so the text of the certificate can't be rebuilt from it. Error-level analysis can miss edits on very high-quality photos; it's one signal among several.</p>
      </div>
    </details>
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
        <p className="tiny faint" style={{ marginBottom: 0 }}><a href={`/api/demo/sample-activity.csv?session=${SESSION}`}>Download the demo export</a> to see the format.</p>
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
      <span className="why">{f.reason}{f.price_note && <> · {f.price_note}</>}{f.owed_back > 0 && <> · <strong style={{ color: 'var(--amber)' }}>{money(f.owed_back)} taken after they died</strong></>}
        {f.next_charge && f.kind === 'SUBSCRIPTION' && <> · <span className={daysTo(f.next_charge) <= 7 ? 'soon' : ''}>next charge {fmtDate(f.next_charge)} ({daysTo(f.next_charge) <= 1 ? 'tomorrow' : `in ${daysTo(f.next_charge)} days`})</span></>}</span>
    </div>
  )
  return (
    <div className="card">
      <p className="sum" style={{ marginTop: 0 }}>We found <strong>{stop.length}</strong> things still charging or billing them: <strong className="num">{money(c.totals.owed_back)}</strong> already taken after {fmtDate(c.date_of_death)}, <strong className="num">{money(c.totals.next_month_at_risk)}</strong> due in the next month, and <strong className="num">{money(c.totals.yearly_at_risk)}</strong> a year if nothing is done.</p>
      <p className="tiny faint" style={{ marginTop: -6 }}>Soonest charge first. Billing periods are estimated from the gaps between charges, allowing for price rises.</p>
      <div className="group-title">We'll ask these to stop and refund</div>
      {stop.map((f) => <Row key={f.id} f={f} canPick />)}
      {leave.length > 0 && <><div className="group-title">We'll leave these alone</div>{leave.map((f) => <Row key={f.id} f={f} />)}</>}
    </div>
  )
}

const TRACK = ['Told', 'Checked', 'Paused', 'Stopped', 'Refunded']
function trackState(f) {
  const s = f.status
  const n = f.notice
  if (s === 'CLOSED') return TRACK.map((_, i) => (i === 2 && f.kind === 'INVOICE' ? 'skip' : 'on'))
  if (s === 'WITHDRAWN') return TRACK.map((_, i) => (i < 2 ? 'on' : ''))
  const checking = s === 'RECEIVED' || n?.status === 'IN_REVIEW' || n?.status === 'ACTIONED'
  const paused = s === 'PAUSED' || n?.paused
  const held = f.kind === 'INVOICE' && paused
  return TRACK.map((_, i) => {
    if (i === 0) return s === 'NOTIFIED' || checking || paused ? 'on' : ''
    if (i === 1) return paused ? 'on' : checking ? 'wait' : ''
    if (i === 2) return held ? 'skip' : paused ? 'on' : ''
    if (i === 3) return paused ? 'wait' : ''
    return ''
  })
}

function useNow(active) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { if (!active) return; const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [active])
  return now
}

function Inbox({ c }) {
  const mail = (c.outbox || []).find((m) => m.kind === 'proof_of_life')
  const [open, setOpen] = useState(false)
  if (!mail) return null
  const link = (mail.body.match(/\/alive\/[\w-]+/) || [])[0]
  return (
    <div className="card inbox">
      <div className="row"><span className="grow small">We also wrote to <strong>{mail.to_addr}</strong>, their own address, in case this report is a mistake.</span>
        <button className="btn quiet sm" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide' : 'See the email'}</button></div>
      {open && (
        <>
          <div className="email-draft" style={{ marginTop: 10 }}><strong>{mail.subject}</strong>{'\n\n'}{mail.body}</div>
          {link && <p className="tiny faint" style={{ margin: '8px 0 0' }}>Demo only: in real use this link exists only in their inbox. <a href={link} target="_blank" rel="noreferrer">Open it as {c.deceased_name}</a></p>}
        </>
      )}
    </div>
  )
}

function Board({ c, windowText }) {
  const told = c.findings.filter((f) => f.notice_id)
  const offline = c.findings.filter((f) => f.status === 'NOT_ON_NETWORK' && f.selected)
  const t = c.totals
  const waiting = told.map((f) => f.notice?.waiting_until).filter(Boolean).sort()[0]
  const now = useNow(!!waiting)
  const left = secondsUntil(waiting, now)
  if (c.status === 'OBJECTED') {
    const resumed = told.reduce((n, f) => n + (f.notice?.outcome?.resumed?.length || 0), 0)
    return (
      <div className="card objected" role="alert">
        <h3 style={{ marginTop: 0 }}>{c.deceased_name} answered: they are alive.</h3>
        <p style={{ margin: 0 }}>Everything stopped. {resumed} paused {resumed === 1 ? 'subscription was' : 'subscriptions were'} resumed, nothing was cancelled and no money moved. Every company got a signed withdrawal.</p>
        <p className="small muted" style={{ marginBottom: 0 }}>This report is now flagged. Any new report by the same person, or about {c.deceased_name}, is blocked until a person looks at it.</p>
      </div>
    )
  }
  return (
    <>
      <p className="headline" aria-live="polite"><strong>{t.closed} of {t.notified}</strong> closed. <span className="num">{money(t.refunded)}</span> on its way back.</p>
      {waiting && left > 0 ? (
        <div className="window" aria-live="polite">
          <span className="clock num">{fmtWait(left)}</span>
          <span><strong>Billing is paused at {t.paused} {t.paused === 1 ? 'company' : 'companies'}.</strong> Nothing has been charged since you pressed send. Cancellations and refunds can't be undone, so they wait {windowText || 'a short while'} in case {c.deceased_name} is alive and this is a mistake.</span>
        </div>
      ) : (
        <p className="muted small" style={{ marginTop: 0 }}>{t.closed === t.notified ? `Every company has replied. ${money(t.yearly_stopped)} a year will no longer be taken.` : 'Each company checks the notice against its own PayPal records before acting.'}
          {t.closed > 0 && <> We re-checked PayPal ourselves: {t.confirmed} of {t.closed} confirmed.</>}</p>
      )}
      <Inbox c={c} />
      <div className="card board" style={{ marginTop: 14 }}>
        {told.map((f) => {
          const st = trackState(f)
          const waitingPerson = (f.notice?.status === 'IN_REVIEW' || f.notice?.status === 'PAUSED') && !f.merchant?.auto_close
          return (
            <div className="m" key={f.id}>
              <span className="m-name">{f.merchant_name}</span>
              <span className="num small">{f.status === 'CLOSED' ? (f.kind === 'INVOICE' ? 'invoice cancelled' : f.refunded ? `${money(f.refunded)} refunded` : f.next_charge ? `renewal on ${fmtDate(f.next_charge)} stopped` : 'stopped') : waitingPerson ? <span style={{ color: 'var(--amber)' }}>{f.notice?.paused ? 'billing paused · a person is reviewing' : 'a person is reviewing'}</span> : f.status === 'PAUSED' ? (f.kind === 'INVOICE' ? 'held, nothing owed' : 'billing paused') : ''}</span>
              <div className="track">{TRACK.map((label, i) => <span key={label} className={st[i]}>{f.kind === 'INVOICE' && i === 4 ? 'Settled' : label}</span>)}</div>
              {f.status === 'CLOSED' && (
                <div className="proofs">
                  {f.notice?.receipt_valid && <span className="proof">✓ Receipt signed by {f.merchant_name}</span>}
                  {f.notice?.confirmation?.all_confirmed && <span className="proof" title={f.notice.confirmation.items.map((i) => `${i.what}: ${i.paypal_status}`).join('\n')}>✓ Confirmed on PayPal</span>}
                  {f.notice?.outcome?.emails_stopped && <span className="proof">✓ No more emails to them</span>}
                </div>
              )}
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
        <select id="ll" style={{ width: 'auto' }} value={lang} onChange={(e) => setLang(e.target.value)}>{LANGS.map(([k, l]) => <option key={k} value={k} disabled={!runtime.live && !OFFLINE_LANGS.includes(k)}>{langLabel(k, l)}</option>)}</select>
        <span className="grow" />
        <a className="btn" href={`/api/cases/${c.id}/record.pdf?session=${SESSION}`} target="_blank" rel="noreferrer">Download the record (PDF)</a>
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

export default function Family({ notify, status }) {
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
  // One click for a judge in a hurry: runs the same four real steps a family would, pausing so each can be read.
  const [auto, setAuto] = useState('')
  const autoplay = async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms))
    const show = (i) => document.querySelectorAll('.step')[i]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    try {
      setAuto('Rachel tells us about her mother, Eleanor…')
      let k = await api.post('/api/cases', { claimant_name: 'Rachel Hayes', claimant_email: 'rachel.hayes@example.com', relationship: 'daughter', language: 'en', deceased_email: '' })
      store.set(k.id); setC(k); await wait(1200); show(1)
      setAuto('Gemini reads the death certificate; eleven checks run…')
      await api.form(`/api/cases/${k.id}/certificate`, { use_specimen: '1' }); await load(k.id); await wait(3500); show(2)
      setAuto("Finding everything still charging Eleanor's PayPal account…")
      await api.form(`/api/cases/${k.id}/activity`, { use_sample: '1' }); await load(k.id); await wait(4500); show(3)
      setAuto('Sending one signed notice to every company…')
      setC(await api.post(`/api/cases/${k.id}/notify`)); await wait(600); show(3)
      setAuto('Billing is paused. Cancellations and refunds wait for the objection window, then run on their own. Watch the board.')
      await wait(9000)
    } catch (e) { notify(e.message, true) } finally { setAuto('') }
  }
  const restart = async () => {
    if (!confirm('Start the demo again from the beginning?')) return
    try { await api.post('/api/demo/reset') } catch { /* ignore */ }
    store.set(null); setC(null); scrollTo(0, 0)
  }

  const verified = c && c.verification && c.verification.score >= 0.8 && !(c.verification.blocking || []).length
  const sent = c && ['NOTIFIED', 'CLOSED', 'OBJECTED'].includes(c.status)
  const toTell = c ? c.findings.filter((f) => f.selected && f.merchant_id && !f.notice_id).length : 0
  const offline = c ? c.findings.filter((f) => f.selected && f.status === 'NOT_ON_NETWORK').length : 0

  return (
    <main className="column">
      <div className="intro">
        <h1>When someone dies, their subscriptions don't.</h1>
        <p>Tell us once. We read the certificate, find everything still charging their PayPal account, and send every company one verified notice. They stop the charges and refund what was taken after the day they died. You don't have to phone anyone.</p>
        {!c && !auto && <p style={{ margin: '14px 0 0' }}><button className="btn primary" onClick={autoplay}>▶ Run the 60-second demo for me</button> <span className="small muted">or fill it in yourself below</span></p>}
      </div>
      {auto && <div className="autobar" role="status" aria-live="polite"><span className="autodot" aria-hidden="true" />{auto}</div>}

      <Step n="1" title="About you" done={!!c}>
        {c ? <p className="muted" style={{ margin: 0 }}>{c.claimant_name}, {c.relationship}. Replies go to {c.claimant_email}.</p> : <About onStart={start} busy={busy} />}
      </Step>

      <Step n="2" title="The death certificate" done={!!c?.certificate_sha256 && verified} locked={!c}>
        {c && <Certificate c={c} refresh={refresh} notify={notify} />}
      </Step>

      <Step n="3" title="What's still charging them" done={!!c?.findings?.length} locked={!c?.certificate_sha256}>
        {c?.certificate_sha256 && <Activity c={c} refresh={refresh} notify={notify} />}
        {c?.findings?.length > 0 && <Suspense fallback={null}><Timeline caseId={c.id} version={`${c.status}-${c.totals.closed}-${c.totals.paused}`} /></Suspense>}
      </Step>

      <Step n="4" title="Tell them, once" done={sent} locked={!c?.findings?.length}>
        {c?.findings?.length > 0 && (sent ? <Board c={c} windowText={status?.objection_window_text} /> : (
          <div className="card">
            {verified ? (
              <>
                <p style={{ marginTop: 0 }}>Each company gets a notice signed by Afterword with only what it needs: name, PayPal email, date of death and the payments involved. Not the certificate. Billing is paused straight away; anything that can't be undone waits a short while in case this is a mistake.</p>
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

      <Step n="5" title="A letter for you to keep" done={c?.status === 'CLOSED'} locked={!sent || c?.status === 'OBJECTED'}>
        {sent && c.status !== 'OBJECTED' && <Letter c={c} notify={notify} />}
      </Step>

      <Step n="6" title="Close the PayPal account itself" locked={!sent || c?.status === 'OBJECTED'}>
        {sent && c.status !== 'OBJECTED' && (
          <div className="card">
            <p style={{ marginTop: 0 }}>PayPal closes a deceased person's account through its own process, and only the executor or administrator can ask. We've filled in the cover sheet it asks for and listed what to attach.</p>
            <a className="btn" href={`/api/cases/${c.id}/paypal-packet.pdf?session=${SESSION}`} target="_blank" rel="noreferrer">Download the PayPal cover sheet (PDF)</a>
          </div>
        )}
      </Step>

      {c && <p className="tiny faint" style={{ marginTop: 60 }}>All names, companies and documents in this demo are fictional. <button className="btn quiet sm" onClick={restart}>Start the demo again</button></p>}
    </main>
  )
}
