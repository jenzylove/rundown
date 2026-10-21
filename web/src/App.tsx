import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { bug, Pitch, readTheme, renderSegment } from './renderer'
import type { Candidate, FeedItem, MatchEvent, Meta, Rundown, RunSnapshot, Segment, TrailItem } from './types'

type View = 'live' | 'studio'
type Decision = 'pending' | 'approved' | 'dropped'

const KIND_LABEL: Record<string, string> = {
  press_up: 'Press', flank_overload: 'Flank', fatigue: 'Fatigue', deep_block: 'Deep block',
  tempo_up: 'Tempo', goal: 'Goal', big_chance: 'Chance',
}

function clockText(clock: number, period: number) {
  const base = period === 1 ? 45 * 60 : 90 * 60
  const m = Math.floor(clock / 60), s = Math.floor(clock % 60)
  if (clock > base) {
    const over = clock - base
    return `${period === 1 ? 45 : 90}+${Math.floor(over / 60) + 1}'`
  }
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function shirtNumbers(meta: Meta, events: MatchEvent[]): number[] {
  const byId: Record<string, number> = {}
  for (const c of [meta.home, meta.away]) for (const p of c.players) byId[p.id] = p.number
  const slots = [...meta.home.starters, ...meta.away.starters]
  for (const e of events) if (e.type === 'substitution') slots[e.slot_index as number] = e.player_on as string
  return slots.map(id => byId[id])
}

export default function App() {
  const [run, setRun] = useState<RunSnapshot | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [trail, setTrail] = useState<TrailItem[]>([])
  const [picks, setPicks] = useState<Candidate[]>([])
  const [status, setStatus] = useState('idle')
  const [score, setScore] = useState<[number, number]>([0, 0])
  const [clock, setClock] = useState({ t: 0, period: 1 })
  const [rundown, setRundown] = useState<Rundown | null>(null)
  const [cut, setCut] = useState('neutral')
  const [lang, setLang] = useState('en')
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [view, setView] = useState<View>('live')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const frameRef = useRef<number[] | null>(null)
  const eventsRef = useRef<MatchEvent[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  const start = useCallback(async (seed?: number) => {
    setBusy(true); setError(null)
    try {
      wsRef.current?.close()
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ seed, speed: 8 }) })
      if (!r.ok) throw new Error(`Could not start a match (${r.status})`)
      const snap: RunSnapshot = await r.json()
      setRun(snap); setFeed([]); setTrail([]); setPicks([]); setRundown(null); setDecisions({}); setStatus(snap.status)
      setScore([0, 0]); setView('live'); eventsRef.current = []; frameRef.current = null
      const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/runs/${snap.id}/ws`)
      wsRef.current = ws
      ws.onmessage = ev => {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'frames') {
          const last = msg.frames[msg.frames.length - 1] as number[]
          frameRef.current = last.slice(1)
          setClock({ t: last[0], period: msg.period })
        } else if (msg.type === 'events') {
          eventsRef.current = eventsRef.current.concat(msg.events)
          for (const e of msg.events as MatchEvent[]) if (e.type === 'goal') {
            const [h, a] = String(e.score).split('-').map(Number); setScore([h, a])
          }
        } else if (msg.type === 'feed') setFeed(f => [...f, msg.item].slice(-80))
        else if (msg.type === 'trail') setTrail(t => [...t, msg.item].slice(-40))
        else if (msg.type === 'provisional') setPicks(msg.picks)
        else if (msg.type === 'status') setStatus(msg.status)
        else if (msg.type === 'rundown') { setRundown(msg.rundown); setStatus('half_time') }
        else if (msg.type === 'snapshot') setFeed(msg.run.feed)
      }
      ws.onerror = () => setError('Lost the live stream. Start a new match to reconnect.')
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }, [])

  useEffect(() => { start() }, [start])

  const skip = async () => { if (run) await fetch(`/api/runs/${run.id}/skip`, { method: 'POST' }) }

  const loadCut = async (c: string, l: string) => {
    if (!run) return
    setCut(c); setLang(l)
    const r = await fetch(`/api/runs/${run.id}/rundown?cut=${c}&lang=${l}`)
    if (r.ok) { setRundown(await r.json()); setDecisions({}) }
  }

  const swap = async (segment_id: string) => {
    if (!run) return
    const r = await fetch(`/api/runs/${run.id}/rundown/swap`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ cut, lang, segment_id }) })
    if (r.ok) { setRundown(await r.json()); setDecisions(d => ({ ...d, [segment_id]: 'pending' })) }
    else setError((await r.json()).detail ?? 'Swap failed')
  }

  const [voicing, setVoicing] = useState(false)
  const goStudio = async () => {
    if (!run || !rundown) return
    setVoicing(true)
    try {  // voice the cut first; the studio still plays, captions only, if the voice is unavailable
      const r = await fetch(`/api/runs/${run.id}/rundown/voice?cut=${cut}&lang=${lang}`, { method: 'POST' })
      if (r.ok) setRundown(await r.json())
    } finally { setVoicing(false); setView('studio') }
  }

  const lockItem = feed.find(f => f.kind === 'locked')
  const onAir = useMemo(() => rundown?.segments.filter(s => decisions[s.segment_id] !== 'dropped') ?? [], [rundown, decisions])
  const studioRundown = useMemo(() => rundown ? { ...rundown, segments: onAir } : null, [rundown, onAir])
  const numbers = useMemo(() => run ? shirtNumbers(run.meta, eventsRef.current) : [], [run, view])

  return (
    <div className="app">
      <header className="top">
        <div className="mark">RUN<span>DOWN</span></div>
        {run && <div className="scorebug">
          <span className="club"><i style={{ background: run.meta.home.colour }} />{run.meta.home.short}</span>
          <b>{score[0]}–{score[1]}</b>
          <span className="club"><i style={{ background: run.meta.away.colour }} />{run.meta.away.short}</span>
          <span className="clock">{status === 'half_time' ? 'HT' : clockText(clock.t, clock.period)}</span>
        </div>}
        <div className="actions">
          {status === 'first_half' && <button onClick={skip} className="ghost">Skip to half-time</button>}
          <button onClick={() => start()} disabled={busy}>New match</button>
        </div>
      </header>
      {error && <div className="error" role="alert">{error}</div>}

      {view === 'live' && <main className="live">
        <section className="stage">
          <LivePitch frameRef={frameRef} eventsRef={eventsRef} meta={run?.meta} clock={clock} status={status} />
          <p className="caption-note">Seed {run?.seed ?? '…'} · synthetic match, fictional clubs · 8× speed</p>
        </section>
        <aside className="desk">
          {!rundown ? <Building feed={feed} picks={picks} run={run} trail={trail} status={status} /> :
            <Desk rundown={rundown} decisions={decisions} setDecisions={setDecisions} swap={swap}
              cut={cut} lang={lang} loadCut={loadCut} lockItem={lockItem} run={run}
              goStudio={goStudio} voicing={voicing} onAirCount={onAir.length} />}
        </aside>
      </main>}

      {view === 'studio' && studioRundown && run && <Studio rundown={studioRundown} meta={run.meta}
        numbers={numbers} back={() => setView('live')} />}
    </div>
  )
}

function LivePitch({ frameRef, eventsRef, meta, clock, status }: {
  frameRef: React.RefObject<number[] | null>; eventsRef: React.RefObject<MatchEvent[]>; meta?: Meta
  clock: { t: number; period: number }; status: string
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const clockRef = useRef(clock); clockRef.current = clock
  const statusRef = useRef(status); statusRef.current = status
  useEffect(() => {
    const c = ref.current!
    const p = new Pitch(c)
    let raf = 0
    const onResize = () => p.resize()
    window.addEventListener('resize', onResize)
    const draw = () => {
      p.theme = readTheme(c)
      if (meta) { p.colours = [meta.home.colour, meta.away.colour]; p.inks = ['#0b1a12', '#ffffff'] }
      p.drawPitch(statusRef.current === 'half_time' ? 0.35 : 0)
      const f = frameRef.current
      if (f && meta) p.drawPlayers(f, null, shirtNumbers(meta, eventsRef.current ?? []))
      if (meta) bug(p, `${meta.home.code} ${statusRef.current === 'half_time' ? 'HT' : clockText(clockRef.current.t, clockRef.current.period)} ${meta.away.code}`, false)
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [meta, frameRef, eventsRef])
  return <canvas ref={ref} className="pitch" aria-label="Live match, top-down view" />
}

function Strength({ v }: { v: number }) {
  return <span className="strength" aria-label={`strength ${Math.round(v * 100)}%`}><i style={{ width: `${Math.round(v * 100)}%` }} /></span>
}

const AGENT_LABEL: Record<string, string> = {
  analyst: 'Analyst', producer: 'Producer', telestrator: 'Telestrator', writer: 'Writer', verifier: 'Verifier',
}

function Building({ feed, picks, run, trail, status }: {
  feed: FeedItem[]; picks: Candidate[]; run: RunSnapshot | null; trail: TrailItem[]; status: string
}) {
  const cands = feed.filter(f => f.kind === 'candidate')
  const work = trail.filter(t => !t.action.startsWith('wrote ') || t.agent === 'writer').slice(-5).reverse()
  return <>
    <div className="desk-head">
      <h2>The rundown</h2>
      <span className="pill live-pill">{status === 'half_time' ? 'Writing the rundown' : 'Building live'}</span>
    </div>
    <p className="muted small">Talking points the crew is tracking. The best three lock at the half-time whistle.</p>
    <ol className="picks">
      {[0, 1, 2].map(i => {
        const c = picks[i]
        return <li key={i} className={c ? '' : 'empty'}>
          <span className="slot">{i + 1}</span>
          {c ? <div><span className={`kind k-${c.kind}`}>{KIND_LABEL[c.kind] ?? c.kind}</span> {c.headline}<Strength v={c.strength} /></div>
            : <div className="muted">Waiting for a story…</div>}
        </li>
      })}
    </ol>
    <h3>Spotted so far</h3>
    <ul className="feed">
      {cands.length === 0 && <li className="muted">The detectors need about eight minutes of play before they compare anything.</li>}
      {[...cands].reverse().map((f, i) => <li key={i}>
        <span className="min">{Math.floor(f.clock / 60)}'</span>
        <span className={`kind k-${f.candidate?.kind}`}>{KIND_LABEL[f.candidate?.kind ?? ''] ?? ''}</span>
        <span>{f.text}</span>
      </li>)}
    </ul>
    {run?.crew.mode === 'agents' && <>
      <h3>Crew at work</h3>
      <ul className="crew-log" aria-live="polite">
        {work.length === 0 && <li className="muted">The Analyst scores each talking point as it is spotted.</li>}
        {work.map(t => <li key={t.seq} className={`st-${t.status}`}>
          <b>{AGENT_LABEL[t.agent] ?? t.agent}</b>
          <span>{t.action}{t.status !== 'ok' && <em> · {t.status}</em>}</span>
          {t.ms !== null && <span className="ms">{(t.ms / 1000).toFixed(1)} s</span>}
        </li>)}
      </ul>
    </>}
    <p className="muted small mode">{run?.crew.mode === 'agents'
      ? <>Crew: <b>agents</b> on {run.crew.model} (Microsoft Agent Framework), checked by the Verifier.</>
      : <>Crew: <b>rules</b> (agents offline until Azure is configured).</>} {run ? `Seed ${run.seed}.` : ''}</p>
  </>
}

function crewLine(r: Rundown) {
  if (r.made_by !== 'crew') return 'rules crew'
  const n = r.segments.length
  const a = (k: 'telestrator' | 'writer') => r.segments.filter(s => s.provenance.crew?.[k] === 'agent').length
  return `agents · pictures ${a('telestrator')}/${n} by the Telestrator, words ${a('writer')}/${n} by the Writer, the rest by the rules crew`
}

function Desk({ rundown, decisions, setDecisions, swap, cut, lang, loadCut, lockItem, goStudio, voicing, onAirCount }: {
  rundown: Rundown; decisions: Record<string, Decision>; setDecisions: (f: (d: Record<string, Decision>) => Record<string, Decision>) => void
  swap: (id: string) => void; cut: string; lang: string; loadCut: (c: string, l: string) => void
  lockItem?: FeedItem; run: RunSnapshot | null; goStudio: () => void; voicing: boolean; onAirCount: number
}) {
  const set = (id: string, d: Decision) => setDecisions(x => ({ ...x, [id]: d }))
  const total = 4 + rundown.segments.filter(s => decisions[s.segment_id] !== 'dropped').reduce((a, s) => a + s.duration_s, 0)
  return <>
    <div className="desk-head">
      <h2>Half-time rundown</h2>
      <span className="pill">Locked{lockItem?.seconds_after_whistle !== undefined ? ` ${lockItem.seconds_after_whistle}s after the whistle` : ''}</span>
    </div>
    <div className="toggles" role="group" aria-label="Cut and language">
      {(['neutral', 'home', 'away'] as const).map(c => <button key={c} className={cut === c ? 'on' : ''} onClick={() => loadCut(c, lang)}>{c === 'neutral' ? 'Neutral' : c === 'home' ? 'Home fans' : 'Away fans'}</button>)}
      <span className="sep" />
      {(['en', 'es'] as const).map(l => <button key={l} className={lang === l ? 'on' : ''} onClick={() => loadCut(cut, l)}>{l.toUpperCase()}</button>)}
    </div>
    <ol className="segments">
      {rundown.segments.map((s, i) => <SegmentRow key={s.segment_id + s.title} s={s} i={i} d={decisions[s.segment_id] ?? 'pending'}
        approve={() => set(s.segment_id, 'approved')} drop={() => set(s.segment_id, decisions[s.segment_id] === 'dropped' ? 'pending' : 'dropped')} swap={() => swap(s.segment_id)} />)}
    </ol>
    <div className="slotfit"><span>Slot 90 s</span><b className={Math.abs(total - 90) <= 3 ? 'ok' : 'warn'}>{total.toFixed(1)} s</b></div>
    <button className="go" onClick={goStudio} disabled={onAirCount === 0 || voicing}>{voicing ? 'Recording the voice-over…' : 'Go to studio ▸'}</button>
    <p className="muted small mode">Built by: <b>{crewLine(rundown)}</b>. Every number comes from the stats engine and links to the match events behind it.</p>
  </>
}

function SegmentRow({ s, i, d, approve, drop, swap }: { s: Segment; i: number; d: Decision; approve: () => void; drop: () => void; swap: () => void }) {
  const [open, setOpen] = useState(false)
  return <li className={`seg d-${d}`}>
    <div className="seg-top">
      <span className="slot">{i + 1}</span>
      <div className="seg-title">
        <span className={`kind k-${s.kind}`}>{KIND_LABEL[s.kind] ?? s.kind}</span>
        <b>{s.title}</b>
        <span className="muted small">{s.duration_s.toFixed(1)} s · {Math.floor(s.clip.t0 / 60)}'</span>
      </div>
    </div>
    <p className="line">“{s.script[1]?.text ?? s.script[0]?.text}”</p>
    {s.provenance.pick_reason && s.provenance.pick_reason !== 'rules ranking' &&
      <p className="why small"><b>Producer:</b> {s.provenance.pick_reason}</p>}
    <div className="seg-actions">
      <button onClick={approve} className={d === 'approved' ? 'on' : ''}>{d === 'approved' ? 'Approved' : 'Approve'}</button>
      <button onClick={swap}>Swap</button>
      <button onClick={drop} className={d === 'dropped' ? 'on' : ''}>{d === 'dropped' ? 'Dropped' : 'Drop'}</button>
      <button className="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>Evidence</button>
    </div>
    {open && s.provenance.why && <p className="why small"><b>Analyst:</b> {s.provenance.why}</p>}
    {open && <ul className="evidence">
      {s.facts.slice(0, 6).map(f => <li key={f.key}><b>{String(f.value)} {f.unit}</b> {f.label}<code>{f.key}</code>{f.evidence.length > 0 && <span className="muted"> · {f.evidence.length} events</span>}</li>)}
    </ul>}
  </li>
}

/** Keep segment k's voice playing in step with the studio clock (re-sync past 150 ms); silence the rest. */
function follow(audios: (HTMLAudioElement | null)[], segs: Segment[], k: number, t: number) {
  audios.forEach((a, i) => {
    if (!a) return
    const v = segs[i].voice!
    const at = t - v.start
    if (i !== k || at < 0 || at > v.audio_s) { if (!a.paused) a.pause(); return }
    if (a.paused) { a.currentTime = at; a.play().catch(() => {}) }
    else if (Math.abs(a.currentTime - at) > 0.15) a.currentTime = at
  })
}

/** How many of the line's words the voice has said by segment time t. */
function spokenTokens(v: NonNullable<Segment['voice']>, line: { t: number; d: number }, t: number) {
  return v.words.filter(w => w.t >= line.t - 0.05 && w.t <= Math.min(t, line.t + line.d + 0.05))
    .reduce((n, w) => n + w.text.split(/\s+/).length, 0)
}

function Studio({ rundown, meta, numbers, back }: { rundown: Rundown; meta: Meta; numbers: number[]; back: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [pos, setPos] = useState({ seg: -1, t: 0 })
  const [playing, setPlaying] = useState(true)
  const playRef = useRef(playing); playRef.current = playing
  const intro = rundown.intro.d
  const segs = rundown.segments
  const total = intro + segs.reduce((a, s) => a + s.duration_s, 0)
  const tRef = useRef(0)
  const audios = useMemo(() => segs.map(s => s.voice ? new Audio(s.voice.url) : null), [segs])
  useEffect(() => () => audios.forEach(a => a?.pause()), [audios])

  useEffect(() => {
    const c = ref.current!
    const p = new Pitch(c)
    p.colours = [meta.home.colour, meta.away.colour]
    let raf = 0, last = performance.now()
    const onResize = () => p.resize()
    window.addEventListener('resize', onResize)
    const draw = (now: number) => {
      const dt = (now - last) / 1000; last = now
      if (playRef.current) tRef.current = Math.min(total, tRef.current + dt)
      p.theme = readTheme(c)
      let t = tRef.current
      if (t < intro) {
        p.drawPitch(0.6)
        const g = p.ctx, s = p.scale
        g.fillStyle = p.theme.accent; g.font = `800 ${s * 3}px 'Barlow Condensed'`; g.textAlign = 'center'; g.textBaseline = 'middle'
        g.fillText(rundown.lang === 'es' ? 'DESCANSO' : 'HALF-TIME', p.sx(52.5), p.sy(24))
        g.fillStyle = p.theme.text; g.font = `800 ${s * 9}px 'Barlow Condensed'`
        g.fillText(`${meta.home.short.toUpperCase()}  ${rundown.score.replace('-', ' – ')}  ${meta.away.short.toUpperCase()}`, p.sx(52.5), p.sy(34))
        g.fillStyle = p.theme.muted; g.font = `500 ${s * 2.6}px Barlow`
        g.fillText(rundown.lang === 'es' ? `${segs.length} claves del primer tiempo` : `${segs.length} things that decided the first half`, p.sx(52.5), p.sy(43))
        setPos(x => x.seg === -1 ? x : { seg: -1, t })
        follow(audios, segs, -1, 0)
      } else {
        t -= intro
        let k = 0
        while (k < segs.length - 1 && t >= segs[k].duration_s) { t -= segs[k].duration_s; k++ }
        const seg = segs[k]
        const tt = Math.min(t, seg.duration_s - 0.001)
        renderSegment(p, seg, tt, numbers, [], `${meta.home.code} ${rundown.score} ${meta.away.code}  ·  ${Math.floor(seg.clip.t0 / 60) + 1}'`)
        follow(audios, segs, playRef.current ? k : -1, tt)
        const a = audios[k]  // read-only probe for the sync check in headless runs
        ;(window as unknown as { __studio?: object }).__studio = {
          k, t: tt, voice: a && !a.paused ? a.currentTime + seg.voice!.start : null }
        setPos(x => (x.seg === k && Math.abs(x.t - tt) < 0.1) ? x : { seg: k, t: tt })
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [rundown, meta, numbers, intro, segs, total, audios])

  const seg = pos.seg >= 0 ? segs[pos.seg] : null
  const line = seg?.script.find(l => pos.t >= l.t && pos.t < l.t + l.d + 0.3)
  const said = line && seg?.voice ? spokenTokens(seg.voice, line, pos.t) : null
  const elapsed = pos.seg < 0 ? pos.t : intro + segs.slice(0, pos.seg).reduce((a, s) => a + s.duration_s, 0) + pos.t
  const jump = (k: number) => { tRef.current = k < 0 ? 0 : intro + segs.slice(0, k).reduce((a, s) => a + s.duration_s, 0); setPlaying(true) }

  return <main className="studio">
    <div className="studio-frame">
      <canvas ref={ref} className="pitch studio-canvas" aria-label="Half-time analysis segment" />
      <div className="captions" aria-live="polite">{line && said !== null
        ? line.text.split(/\s+/).map((w, i) => <span key={i} className={i < said ? 'said' : ''}>{w} </span>)
        : line?.text ?? ''}</div>
    </div>
    <div className="transport">
      <button onClick={back} className="ghost">◂ Desk</button>
      <button onClick={() => { if (tRef.current >= total) tRef.current = 0; setPlaying(!playing) }}>{playing && tRef.current < total ? 'Pause' : 'Play'}</button>
      <div className="chips">
        <button className={pos.seg === -1 ? 'on' : ''} onClick={() => jump(-1)}>Intro</button>
        {segs.map((s, k) => <button key={s.segment_id} className={pos.seg === k ? 'on' : ''} onClick={() => jump(k)}>{k + 1}. {s.title}</button>)}
      </div>
      <span className="time">{elapsed.toFixed(1)} / {total.toFixed(1)} s</span>
    </div>
    <p className="muted small">{segs.some(s => s.voice)
      ? 'Voice-over by Azure AI Speech; captions follow the voice word by word.'
      : 'Captions show the presenter script (voice-over unavailable).'} Animations are drawn live from the match tracking, not pre-rendered.</p>
  </main>
}
