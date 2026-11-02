import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Family from './Family'
import Merchant from './Merchant'

export default function App() {
  const [world, setWorld] = useState(() => (location.hash === '#business' ? 'merchant' : 'family'))
  const [status, setStatus] = useState(null)
  const [toast, setToast] = useState(null)

  const notify = useCallback((msg, err = false) => {
    setToast({ msg, err })
    setTimeout(() => setToast(null), err ? 6000 : 3200)
  }, [])

  useEffect(() => { api.get('/api/status').then(setStatus).catch(() => {}) }, [])
  useEffect(() => { history.replaceState(null, '', world === 'merchant' ? '#business' : '#') }, [world])

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
        </div>
        <span className="spacer" />
        {status && (
          <div className="status">
            <span><i className="dot" style={{ background: status.paypal_mode === 'sandbox' ? '#2f5d52' : '#8f621a' }} />
              PayPal {status.paypal_mode === 'sandbox' ? 'sandbox' : 'simulator'}</span>
            <span><i className="dot" style={{ background: status.llm_live ? '#2f5d52' : '#8f621a' }} />{status.llm}</span>
          </div>
        )}
      </header>
      {world === 'family' ? <Family notify={notify} /> : <Merchant notify={notify} />}
      {toast && <div role="status" className={`toast ${toast.err ? 'err' : ''}`}>{toast.msg}</div>}
    </>
  )
}
