import { useEffect, useRef, useState } from 'react'
import { Scheduler, StringHelper } from '@bryntum/scheduler'
import '@bryntum/scheduler/scheduler.css'
import '@bryntum/scheduler/svalbard-light.css'
import '@bryntum/scheduler/fontawesome/css/fontawesome.css'
import '@bryntum/scheduler/fontawesome/css/solid.css'
import { api } from './api'

// The family's view of the money over time, drawn with Bryntum Scheduler: one row per company,
// what was charged before and after the death, when Afterword told them and when it closed,
// and every charge in the next year that will no longer be taken.
const LEGEND = [['before', 'Charged before'], ['after', 'Charged after the death'], ['told', 'Told by Afterword'],
  ['closed', 'Closed'], ['future', 'Won’t be taken'], ['risk', 'Still due'], ['death', 'Date of death'], ['today', 'Today']]

export default function Timeline({ caseId, version }) {
  const host = useRef(null)
  const sched = useRef(null)
  const [data, setData] = useState(null)

  useEffect(() => { api.get(`/api/cases/${caseId}/timeline`).then(setData).catch(() => {}) }, [caseId, version])

  useEffect(() => {
    if (!data || !host.current || !data.resources.length) return
    const events = data.events.map((e) => ({ ...e, cls: `aw-${e.kind}${e.kind === 'future' && !e.stopped ? ' aw-risk' : ''}` }))
    if (sched.current) {
      sched.current.resources = data.resources
      sched.current.events = events
      sched.current.timeRanges = data.timeRanges
      return
    }
    sched.current = new Scheduler({
      appendTo: host.current,
      readOnly: true,
      startDate: data.start,
      endDate: data.end,
      viewPreset: 'year',
      forceFit: true,
      rowHeight: 46,
      barMargin: 10,
      eventStyle: 'filled',
      milestoneTextPosition: 'always-outside',
      zoomOnMouseWheel: false,
      columns: [{ text: 'Company', field: 'name', width: 150 }],
      features: {
        timeRanges: { showCurrentTimeLine: false, showHeaderElements: false },
        eventTooltip: { template: ({ eventRecord }) => `${StringHelper.encodeHtml(eventRecord.name)}<br><span class="aw-sub">${eventRecord.startDate.toLocaleDateString()}</span>` },
        eventMenu: false, scheduleMenu: false, cellMenu: false, headerMenu: false, eventEdit: false, eventDrag: false, eventResize: false, eventDragCreate: false,
      },
      eventRenderer: () => '',
      resources: data.resources,
      events,
      timeRanges: data.timeRanges,
    })
    return undefined
  }, [data])

  useEffect(() => () => { sched.current?.destroy(); sched.current = null }, [])

  if (!data || !data.resources.length) return null
  const prevented = data.events.filter((e) => e.kind === 'future' && e.stopped).length
  return (
    <div className="card timeline">
      <div className="row">
        <h3 className="grow" style={{ margin: 0 }}>The money, over time</h3>
        {prevented > 0 && <span className="small" style={{ color: 'var(--moss)' }}><strong>{prevented}</strong> charges in the next year won't be taken</span>}
      </div>
      <div className="legend">{LEGEND.map(([k, l]) => <span key={k}><i className={`dot aw-${k}`} />{l}</span>)}</div>
      <div ref={host} className="tl-host" />
    </div>
  )
}
