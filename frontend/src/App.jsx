import { useCallback, useEffect, useState } from 'react'
import { api, runtime } from './api'
import Family from './Family'
import Merchant from './Merchant'
import Lab from './Lab'
import Alive from './Alive'
import Tour from './Tour'

export default function App() {
  const [world, setWorld] = useState(() => ({ '#business': 'merchant', '#lab': 'lab' }[location.hash] || 'family'))
  const alive = location.pathname.match(/^\/alive\/([\w-]+)/)
  const [status, setStatus] = useState(null)
  const [toast, setToast] = useState(null)

  const notify = useCallback((msg, err = false) => {
    setToast({ msg, err })
    setTimeout(() => setToast(null), err ? 6000 : 3200)
  }, [])

  useEffect(() => { api.get('/api/status').then((s) => { runtime.live = !!s.llm_live; setStatus(s) }).catch(() => {}) }, [])
  useEffect(() => { if (!alive) history.replaceState(null, '', { merchant: '#business', lab: '#lab' }[world] || '#') }, [world, alive])

  return (
    <>
      <header className="top">
        <div className="mark">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M7 24 C12 8, 20 8, 25 24" fill="none" stroke="#2f5d52" strokeWidth="2.6" strokeLinecap="round" />
            <circle cx="16" cy="9" r="2.4" fill="#2f5d52" />
          </svg>
          Afterword
        </div>
        <div className="world" role="group" aria-label="Who is using Afterword">
          <button aria-pressed={world === 'family'} onClick={() => setWorld('family')}>For families</button>
          <button aria-pressed={world === 'merchant'} onClick={() => setWorld('merchant')}>For businesses</button>
          <button aria-pressed={world === 'lab'} onClick={() => setWorld('lab')}>Try to break it</button>
        </div>
        <span className="spacer" />
        {status && (
          <div className="status">
            <span title={status.paypal_mode === 'sandbox' ? 'Every PayPal call goes to the real sandbox'
              : status.paypal_keys ? 'The shared demo runs on a PayPal simulator so every visitor gets a fresh run; For businesses > PayPal sandbox proof calls the real sandbox'
              : 'No PayPal keys on this server: the demo runs on a PayPal simulator'}>
              <i className="dot" style={{ background: status.paypal_mode === 'sandbox' || status.paypal_keys ? '#2f5d52' : '#8f621a' }} />
              {status.paypal_mode === 'sandbox' ? 'PayPal sandbox' : status.paypal_keys ? 'PayPal sandbox connected · demo on simulator' : 'PayPal simulator'}</span>
            <span><i className="dot" style={{ background: status.llm_live ? '#2f5d52' : '#8f621a' }} />{status.llm_live ? `AI: ${status.llm}` : 'AI offline (no key)'}</span>
          </div>
        )}
      </header>
      {!alive && <Tour go={(w) => { setWorld(w); window.scrollTo({ top: 0, behavior: 'smooth' }) }} />}
      {alive ? <Alive token={alive[1]} /> : world === 'family' ? <Family go={setWorld} notify={notify} status={status} /> : world === 'lab' ? <Lab notify={notify} /> : <Merchant notify={notify} status={status} />}
      {toast && <div role="status" className={`toast ${toast.err ? 'err' : ''}`}>{toast.msg}</div>}
    </>
  )
}
