import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { postRun, rememberOwner } from './owners'
import { bug, Pitch, readTheme, renderSegment, renderSegment3D } from './renderer'
import { Stadium, webglAvailable } from './stadium'
import type { Candidate, FeedItem, MatchEvent, Meta, Phase, Profile, Rundown, RunSnapshot, Segment, TrailItem, WallItem } from './types'

type View = 'live' | 'studio'
type Decision = 'pending' | 'approved' | 'dropped'
type Lang = Profile['lang']

const KIND_LABEL: Record<string, string> = {
  press_up: 'Press', flank_overload: 'Flank', fatigue: 'Fatigue', deep_block: 'Deep block',
  tempo_up: 'Tempo', goal: 'Goal', big_chance: 'Chance', player_focus: 'Your player',
}
const LANG_NAME: Record<Lang, string> = { en: 'English', es: 'Español', fr: 'Français', pt: 'Português' }
const MASTER: Profile = { side: 'neutral', lang: 'en', depth: 'analyst', player: null }
const DEFAULT_FAN: Profile = { side: 'neutral', lang: 'en', depth: 'casual', player: null }
const SINCE_DEFAULT_S = 600  // "catch me up" with no known absence covers the last ten minutes

const keyOf = (p: Profile) => `${p.side}|${p.lang}|${p.depth}|${p.player ?? '-'}`
const qs = (p: Profile) => `cut=${p.side}&lang=${p.lang}&depth=${p.depth}${p.player ? `&player=${p.player}` : ''}`
const body = (p: Profile) => ({ cut: p.side, lang: p.lang, depth: p.depth, player: p.player })

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

function playerName(meta: Meta | undefined, id: string | null | undefined) {
  if (!meta || !id) return null
  return [...meta.home.players, ...meta.away.players].find(p => p.id === id)?.name ?? null
}

/** "Harbour fan · Casual · FR · follows Andrei Lund" */
export function fanLabel(p: Profile, meta?: Meta, name?: string | null) {
  const side = p.side === 'neutral' ? 'Neutral' : `${(p.side === 'home' ? meta?.home.short : meta?.away.short) ?? p.side} fan`
  const who = name ?? playerName(meta, p.player)
  return [side, p.depth === 'casual' ? 'Casual' : 'Analyst', p.lang.toUpperCase(), who ? `follows ${who}` : null].filter(Boolean).join(' · ')
}

function loadFan(): Profile | null {
  try { const s = localStorage.getItem('rundown.fan'); return s ? JSON.parse(s) : null } catch { return null }
}
function saveFan(p: Profile) {
  try { localStorage.setItem('rundown.fan', JSON.stringify(p)) } catch { /* private window: the choice lasts this visit */ }
}

export default function App() {
  const [run, setRun] = useState<RunSnapshot | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [trail, setTrail] = useState<TrailItem[]>([])
  const [picks, setPicks] = useState<Candidate[]>([])
  const [status, setStatus] = useState('idle')
  const [score, setScore] = useState<[number, number]>([0, 0])
  const [clock, setClock] = useState({ t: 0, period: 1 })
  const [rundowns, setRundowns] = useState<Record<string, Rundown>>({})
  const [wall, setWall] = useState<WallItem[]>([])
  const [fan, setFan] = useState<Profile | null>(null)
  const [picking, setPicking] = useState(false)
  const [showing, setShowing] = useState<Profile | null>(null)  // the cut on the desk: the fan's own, or another fan's
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [view, setView] = useState<View>('live')
  const [onStage, setOnStage] = useState<Rundown | null>(null)
  const [busy, setBusy] = useState(false)
  const [making, setMaking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)  // information, not a failure: fades on its own
  const [missed, setMissed] = useState<number | null>(null)  // match clock when the fan looked away
  const frameRef = useRef<number[] | null>(null)
  const eventsRef = useRef<MatchEvent[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const clockRef = useRef(clock); clockRef.current = clock
  const runRef = useRef(run); runRef.current = run
  const fanRef = useRef(fan); fanRef.current = fan

  const watch = useCallback(async (runId: string, p: Profile) => {
    await fetch(`/api/runs/${runId}/watch`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body(p)) })
  }, [])

  const start = useCallback(async (seed?: number) => {
    setBusy(true); setError(null)
    try {
      wsRef.current?.close()
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ seed, speed: 8 }) })
      if (!r.ok) throw new Error(`Could not start a match: ${(await r.json().catch(() => ({}))).detail ?? r.status}`)
      const snap: RunSnapshot & { owner?: string } = await r.json()
      rememberOwner(snap.id, snap.owner)
      setRun(snap); setFeed([]); setTrail([]); setPicks([]); setRundowns({}); setWall([]); setDecisions({}); setStatus(snap.status)
      setScore([0, 0]); setView('live'); setOnStage(null); setMissed(null); eventsRef.current = []; frameRef.current = null
      // a returning fan keeps their side, language and depth; squads change every match, so not the player
      const saved = loadFan()
      const known: Profile | null = saved ? { ...saved, player: null } : null
      setFan(known); setShowing(known); setPicking(!known)
      if (known) watch(snap.id, known)
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
        else if (msg.type === 'status') {
          setStatus(msg.status)
          if (msg.status === 'failed') setError(`This match stopped: ${msg.error ?? 'the match stream failed'}. Start a new match.`)
        }
        else if (msg.type === 'rundown') setRundowns(rs => ({ ...rs, [`${msg.phase}|${msg.key}`]: msg.rundown }))
        else if (msg.type === 'wall') setWall(w => [...w.filter(x => x.key !== msg.item.key), msg.item])
        else if (msg.type === 'snapshot') { setFeed(msg.run.feed); setWall(msg.run.wall ?? []) }
      }
      ws.onerror = () => setError('Lost the live stream. Start a new match to reconnect.')
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }, [watch])

  useEffect(() => { start() }, [start])

  // a notice never outlives the moment it was about: it fades, and goes at the whistle or a new match
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 8000)
    return () => clearTimeout(t)
  }, [notice])
  useEffect(() => { setNotice(null) }, [status, run])

  // a fan who looks away and comes back gets offered what they missed
  useEffect(() => {
    let awayAt: number | null = null
    const onVis = () => {
      if (document.hidden) awayAt = clockRef.current.t
      else if (awayAt !== null) {
        if (clockRef.current.t - awayAt >= 120) setMissed(awayAt)
        awayAt = null
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  const phase: 'ht' | 'ft' = status === 'full_time' ? 'ft' : 'ht'
  // locked once the studio's own rundown for this whistle has arrived, not merely when the whistle blows
  const locked = (status === 'half_time' || status === 'full_time') && `${phase}|${keyOf(MASTER)}` in rundowns
  const desk = showing ?? fan ?? MASTER
  const current = rundowns[`${phase}|${keyOf(desk)}`] ?? null

  // the cut on the desk is made on request when nobody has made it yet
  useEffect(() => {
    if (!run || !locked || current) return
    const k = `${phase}|${keyOf(desk)}`
    let gone = false
    setMaking(k)
    fetch(`/api/runs/${run.id}/rundown?${qs(desk)}&phase=${phase}`).then(async r => {
      if (gone) return
      if (r.ok) { const rd: Rundown = await r.json(); setRundowns(rs => ({ ...rs, [k]: rd })) }
      else setError((await r.json().catch(() => ({}))).detail ?? 'Could not make that cut')
    }).finally(() => { if (!gone) setMaking(m => m === k ? null : m) })
    return () => { gone = true }
  }, [run, locked, phase, desk, current])

  const chooseFan = async (p: Profile) => {
    setFan(p); setShowing(p); setPicking(false); saveFan(p); setDecisions({})
    if (run) await watch(run.id, p)
  }

  const skip = async () => { if (run) await postRun(`/api/runs/${run.id}/skip`) }
  const secondHalf = async () => {
    if (!run) return
    const r = await postRun(`/api/runs/${run.id}/second-half`)
    if (r.ok) { setStatus((await r.json()).status); setShowing(fan) }
  }

  const swap = async (segment_id: string) => {
    if (!run) return
    const r = await fetch(`/api/runs/${run.id}/rundown/swap`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...body(desk), segment_id, phase }) })
    if (r.ok) { const rd = await r.json(); setRundowns(rs => ({ ...rs, [`${phase}|${keyOf(desk)}`]: rd })); setDecisions(d => ({ ...d, [segment_id]: 'pending' })) }
    else setError((await r.json()).detail ?? 'Swap failed')
  }

  const [voicing, setVoicing] = useState(false)
  const goStudio = async () => {
    if (!run || !current) return
    setVoicing(true)
    let rd = current
    try {  // voice the cut first; the studio still plays, captions only, if the voice is unavailable
      const r = await fetch(`/api/runs/${run.id}/rundown/voice?${qs(desk)}&phase=${phase}`, { method: 'POST' })
      if (r.ok) { rd = await r.json(); setRundowns(rs => ({ ...rs, [`${phase}|${keyOf(desk)}`]: rd })) }
    } finally {
      setVoicing(false)
      setOnStage({ ...rd, phase, segments: rd.segments.filter(s => decisions[s.segment_id] !== 'dropped') })
      setView('studio')
    }
  }

  const [catching, setCatching] = useState(false)
  const catchUp = async (since?: number) => {
    if (!run) return
    const p = fan ?? DEFAULT_FAN
    const from = since ?? Math.max(clock.period === 1 ? 0 : 2700, clock.t - SINCE_DEFAULT_S)
    setCatching(true); setMissed(null); setError(null); setNotice(null)
    try {
      const r = await fetch(`/api/runs/${run.id}/catchup`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...body(p), since_s: from }) })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? 'Could not catch you up')
      const rd: Rundown = await r.json()
      if (!rd.segments.length) setNotice(clock.period === 1 && clock.t < 1080
        ? `You're all caught up: ${score[0]}–${score[1]}. The crew compares each ten minutes with what came before, so the first talking points arrive about eighteen minutes in.`
        : `You're all caught up: ${score[0]}–${score[1]}, and nothing in that spell was worth a segment.`)
      else { setOnStage({ ...rd, phase: 'catchup' }); setView('studio') }
    } catch (e) { setError((e as Error).message) } finally { setCatching(false) }
  }

  const lockItem = [...feed].reverse().find(f => f.kind === 'locked')
  const onAirCount = current?.segments.filter(s => decisions[s.segment_id] !== 'dropped').length ?? 0
  const numbers = useMemo(() => run ? shirtNumbers(run.meta, eventsRef.current) : [], [run, view])
  const playing = status === 'first_half' || status === 'second_half'
  const phaseWall = wall.filter(w => w.phase === phase)

  return (
    <div className="app">
      <header className="top">
        <a className="mark" href="/">RUN<span>DOWN</span></a>
        {run && <div className="scorebug">
          <span className="club"><i style={{ background: run.meta.home.colour }} />{run.meta.home.short}</span>
          <b>{score[0]}–{score[1]}</b>
          <span className="club"><i style={{ background: run.meta.away.colour }} />{run.meta.away.short}</span>
          <span className="clock">{status === 'half_time' ? 'HT' : status === 'full_time' ? 'FT' : clockText(clock.t, clock.period)}</span>
        </div>}
        {run && fan && !picking && <button className="fan-badge" onClick={() => setPicking(true)} title="Change who you're watching as">
          <span className="muted">Your show</span> {fanLabel(fan, run.meta)}
        </button>}
        <nav className="nav"><a href={run ? `/proof?run=${run.id}` : '/proof'}>Proof</a></nav>
        <div className="actions">
          {playing && <button onClick={() => catchUp()} className="ghost" disabled={catching || clock.t - (clock.period === 1 ? 0 : 2700) < 60}>
            {catching ? 'Catching you up…' : 'Catch me up'}</button>}
          {status === 'first_half' && <button onClick={skip} className="ghost">Skip to half-time</button>}
          {status === 'half_time' && <button onClick={secondHalf} className="ghost">Kick off the second half</button>}
          <button onClick={() => start()} disabled={busy}>New match</button>
        </div>
      </header>
      {error && <div className="error" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      {missed !== null && playing && <div className="missed" role="status">
        <span>You missed {Math.round((clock.t - missed) / 60)} minutes.</span>
        <button onClick={() => catchUp(missed)} disabled={catching}>{catching ? 'Catching you up…' : 'Catch me up ▸'}</button>
        <button className="ghost" onClick={() => setMissed(null)}>No thanks</button>
      </div>}

      {view === 'live' && <main className="live">
        <section className="stage">
          <LivePitch frameRef={frameRef} eventsRef={eventsRef} meta={run?.meta} clock={clock} status={status} />
          <p className="caption-note">Seed {run?.seed ?? '…'} · synthetic match, fictional clubs · 8× speed</p>
        </section>
        <aside className="desk">
          {run && picking ? <FanCard meta={run.meta} initial={fan ?? DEFAULT_FAN} choose={chooseFan}
            cancel={fan ? () => setPicking(false) : undefined} /> :
            !locked ? <Building feed={feed} picks={status === 'first_half' ? picks : []} run={run} trail={trail} status={status} fan={fan} /> :
            <Desk rundown={current} decisions={decisions} setDecisions={setDecisions} swap={swap}
              desk={desk} fan={fan} setShowing={p => { setShowing(p); setDecisions({}) }} lockItem={lockItem} run={run}
              goStudio={goStudio} voicing={voicing} onAirCount={onAirCount} phase={phase} making={!current && !!making}
              wall={phaseWall} />}
        </aside>
      </main>}

      {view === 'studio' && onStage && run && <Studio rundown={onStage} meta={run.meta}
        numbers={numbers} fan={onStage.profile ?? fan ?? MASTER} back={() => { setView('live'); setOnStage(null) }} />}
    </div>
  )
}

function FanCard({ meta, initial, choose, cancel }: { meta: Meta; initial: Profile; choose: (p: Profile) => void; cancel?: () => void }) {
  const [p, setP] = useState<Profile>(initial)
  const set = (x: Partial<Profile>) => setP(q => ({ ...q, ...x }))
  const squad = p.side === 'home' ? meta.home.players : p.side === 'away' ? meta.away.players : [...meta.home.players, ...meta.away.players]
  const sides: [Profile['side'], string, string | null][] = [
    ['neutral', 'Neutral', null], ['home', meta.home.short, meta.home.colour], ['away', meta.away.short, meta.away.colour]]
  return <form className="fan-card" onSubmit={e => { e.preventDefault(); choose(p) }}>
    <p className="eyebrow">Your half-time show</p>
    <h2>Who's watching?</h2>
    <p className="muted small">The crew makes your own analysis segment while the half plays: your club, your player, your language, your depth.
      It is ready at the whistle.</p>
    <fieldset>
      <legend>Your side</legend>
      <div className="choice">{sides.map(([s, label, colour]) =>
        <button type="button" key={s} className={p.side === s ? 'on' : ''} aria-pressed={p.side === s}
          onClick={() => set({ side: s, player: null })}>{colour && <i style={{ background: colour }} />}{label}</button>)}</div>
    </fieldset>
    <fieldset>
      <legend>How deep</legend>
      <div className="choice two">
        <button type="button" className={p.depth === 'casual' ? 'on' : ''} aria-pressed={p.depth === 'casual'} onClick={() => set({ depth: 'casual' })}>
          <b>Casual</b><span>The story, in plain words</span></button>
        <button type="button" className={p.depth === 'analyst' ? 'on' : ''} aria-pressed={p.depth === 'analyst'} onClick={() => set({ depth: 'analyst' })}>
          <b>Analyst</b><span>The numbers behind it</span></button>
      </div>
    </fieldset>
    <fieldset>
      <legend>Language</legend>
      <div className="choice">{(Object.keys(LANG_NAME) as Lang[]).map(l =>
        <button type="button" key={l} className={p.lang === l ? 'on' : ''} aria-pressed={p.lang === l} onClick={() => set({ lang: l })}>{LANG_NAME[l]}</button>)}</div>
    </fieldset>
    <label className="follow">
      <span>Follow a player</span>
      <select value={p.player ?? ''} onChange={e => set({ player: e.target.value || null })}>
        <option value="">Nobody in particular</option>
        {squad.map(pl => <option key={pl.id} value={pl.id}>{pl.number}. {pl.name} ({pl.role})</option>)}
      </select>
    </label>
    <div className="fan-actions">
      <button type="submit" className="go">Make my show ▸</button>
      {cancel && <button type="button" className="ghost" onClick={cancel}>Keep my show</button>}
    </div>
  </form>
}

const GL = webglAvailable()

function LivePitch({ frameRef, eventsRef, meta, clock, status }: {
  frameRef: React.RefObject<number[] | null>; eventsRef: React.RefObject<MatchEvent[]>; meta?: Meta
  clock: { t: number; period: number }; status: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const clockRef = useRef(clock); clockRef.current = clock
  const statusRef = useRef(status); statusRef.current = status
  useEffect(() => {
    const root = ref.current!
    const flat = !GL
    let st: Stadium | null = null, p: Pitch | null = null
    if (flat) { const c = document.createElement('canvas'); c.className = 'pitch-flat'; root.append(c); p = new Pitch(c) }
    else st = new Stadium(root)
    let raf = 0, first = true
    const onResize = () => { st?.resize(); p?.resize() }
    window.addEventListener('resize', onResize)
    const draw = () => {
      const theme = readTheme(root)
      const status = statusRef.current
      const label = meta ? `${meta.home.code} ${status === 'half_time' ? 'HT' : status === 'full_time' ? 'FT' : clockText(clockRef.current.t, clockRef.current.period)} ${meta.away.code}` : ''
      const f = frameRef.current
      const numbers = meta ? shirtNumbers(meta, eventsRef.current ?? []) : null
      const dim = status === 'half_time' || status === 'full_time' ? 0.35 : 0
      if (st) {
        st.theme = theme
        if (meta) { st.colours = [meta.home.colour, meta.away.colour]; st.inks = ['#0b1a12', '#ffffff'] }
        st.mode = 'live'
        st.begin(dim)
        if (f && meta) st.setFrame(f, null, numbers)
        if (meta) bug(st.hud, label, false)
        st.render(f, first); first = !f
      } else if (p) {
        p.theme = theme
        if (meta) { p.colours = [meta.home.colour, meta.away.colour]; p.inks = ['#0b1a12', '#ffffff'] }
        p.drawPitch(dim)
        if (f && meta) p.drawPlayers(f, null, numbers)
        if (meta) bug(p, label, false)
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize); st?.dispose(); root.replaceChildren() }
  }, [meta, frameRef, eventsRef])
  return <div ref={ref} className="pitch stage3d" role="img" aria-label="Live match, broadcast view" />
}

function Strength({ v }: { v: number }) {
  return <span className="strength" aria-label={`strength ${Math.round(v * 100)}%`}><i style={{ width: `${Math.round(v * 100)}%` }} /></span>
}

const AGENT_LABEL: Record<string, string> = {
  analyst: 'Analyst', producer: 'Producer', telestrator: 'Telestrator', writer: 'Writer', verifier: 'Verifier', voice: 'Voice',
}

function Building({ feed, picks, run, trail, status, fan }: {
  feed: FeedItem[]; picks: Candidate[]; run: RunSnapshot | null; trail: TrailItem[]; status: string; fan: Profile | null
}) {
  const cands = feed.filter(f => f.kind === 'candidate')
  const work = trail.filter(t => !t.action.startsWith('wrote ') || t.agent === 'writer').slice(-5).reverse()
  const second = status === 'second_half'
  return <>
    <div className="desk-head">
      <h2>{second ? 'Second half' : 'The rundown'}</h2>
      <span className="pill live-pill">Building live</span>
    </div>
    <p className="muted small">{second
      ? 'The crew keeps watching. Your full-time show locks at the final whistle; press Catch me up any time.'
      : <>Talking points the crew is tracking. {fan ? <>Your show is being drafted as <b>{fanLabel(fan, run?.meta)}</b>.</> : 'The best three lock at the half-time whistle.'}</>}</p>
    {!second && <ol className="picks">
      {[0, 1, 2].map(i => {
        const c = picks[i]
        return <li key={i} className={c ? '' : 'empty'}>
          <span className="slot">{i + 1}</span>
          {c ? <div><span className={`kind k-${c.kind}`}>{KIND_LABEL[c.kind] ?? c.kind}</span> {c.headline}<Strength v={c.strength} /></div>
            : <div className="muted">Waiting for a story…</div>}
        </li>
      })}
    </ol>}
    <h3>Spotted so far</h3>
    <ul className="feed">
      {cands.length === 0 && <li className="muted">The detectors compare the last ten minutes with what came before, so the first talking points arrive after about eighteen minutes of play.</li>}
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

function Desk({ rundown, decisions, setDecisions, swap, desk, fan, setShowing, lockItem, run, goStudio, voicing, onAirCount, phase, making, wall }: {
  rundown: Rundown | null; decisions: Record<string, Decision>; setDecisions: (f: (d: Record<string, Decision>) => Record<string, Decision>) => void
  swap: (id: string) => void; desk: Profile; fan: Profile | null; setShowing: (p: Profile) => void
  lockItem?: FeedItem; run: RunSnapshot | null; goStudio: () => void; voicing: boolean; onAirCount: number
  phase: 'ht' | 'ft'; making: boolean; wall: WallItem[]
}) {
  const set = (id: string, d: Decision) => setDecisions(x => ({ ...x, [id]: d }))
  const total = rundown ? 4 + rundown.segments.filter(s => decisions[s.segment_id] !== 'dropped').reduce((a, s) => a + s.duration_s, 0) : 0
  const mine = fan && keyOf(fan) === keyOf(desk)
  const flip = (x: Partial<Profile>) => setShowing({ ...desk, ...x, ...(x.side && x.side !== desk.side ? { player: null } : {}) })
  const full = wall.filter(w => w.verified === w.segments).length
  const myWall = wall.find(w => w.key === `${phase}|${keyOf(desk)}`)
  return <>
    <div className="desk-head">
      <h2>{mine ? `Your ${phase === 'ht' ? 'half-time' : 'full-time'} show` : `${phase === 'ht' ? 'Half-time' : 'Full-time'} rundown`}</h2>
      <span className="pill">{myWall ? `Ready ${myWall.ready_s.toFixed(1)} s after the whistle`
        : lockItem?.seconds_after_whistle !== undefined ? `Locked ${lockItem.seconds_after_whistle}s after the whistle` : 'Locked'}</span>
    </div>
    <p className="for-whom">Made for <b>{fanLabel(desk, run?.meta)}</b>{!mine && fan && <> · <button className="link" onClick={() => setShowing(fan)}>back to your show</button></>}</p>
    <div className="toggles" role="group" aria-label="See another fan's show">
      {(['neutral', 'home', 'away'] as const).map(c => <button key={c} className={desk.side === c ? 'on' : ''} onClick={() => flip({ side: c })}>
        {c === 'neutral' ? 'Neutral' : `${c === 'home' ? run?.meta.home.short : run?.meta.away.short} fans`}</button>)}
      <span className="sep" />
      {(['casual', 'analyst'] as const).map(d => <button key={d} className={desk.depth === d ? 'on' : ''} onClick={() => flip({ depth: d })}>{d === 'casual' ? 'Casual' : 'Analyst'}</button>)}
      <span className="sep" />
      {(['en', 'es', 'fr', 'pt'] as const).map(l => <button key={l} className={desk.lang === l ? 'on' : ''} onClick={() => flip({ lang: l })}>{l.toUpperCase()}</button>)}
    </div>
    {!rundown ? <p className="making" aria-live="polite">{making ? 'The crew is making this cut now. It takes a few seconds…' : 'Waiting for this cut…'}</p> : <>
      <ol className="segments">
        {rundown.segments.map((s, i) => <SegmentRow key={s.segment_id + s.title} s={s} i={i} d={decisions[s.segment_id] ?? 'pending'}
          approve={() => set(s.segment_id, 'approved')} drop={() => set(s.segment_id, decisions[s.segment_id] === 'dropped' ? 'pending' : 'dropped')} swap={() => swap(s.segment_id)} />)}
      </ol>
      <div className="slotfit"><span>Slot 90 s</span><b className={Math.abs(total - 90) <= 3 ? 'ok' : 'warn'}>{total.toFixed(1)} s</b></div>
      <button className="go" onClick={goStudio} disabled={onAirCount === 0 || voicing}>{voicing ? 'Recording the voice-over…' : 'Watch it ▸'}</button>
      <p className="muted small mode">Built by: <b>{crewLine(rundown)}</b>. Every number comes from the stats engine and links to the match events behind it.</p>
    </>}
    {wall.length > 0 && <p className="wall-line small">
      The crew has made <b>{wall.length}</b> {wall.length === 1 ? 'cut' : 'cuts'} {phase === 'ht' ? 'this break' : 'since full time'}, <b>{full}</b> fully verified.{' '}
      {run && <a href={`/proof?run=${run.id}`}>See the whistle wall</a>}</p>}
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
        <span className="muted small">{s.duration_s.toFixed(1)} s · {Math.floor(s.clip.t0 / 60)}'{s.provenance.verified && <> · <span className="ok-mark">✓ verified</span></>}</span>
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

const TITLE: Record<Phase, Record<Lang, string>> = {
  ht: { en: 'HALF-TIME', es: 'DESCANSO', fr: 'MI-TEMPS', pt: 'INTERVALO' },
  ft: { en: 'FULL TIME', es: 'FINAL', fr: 'FIN DU MATCH', pt: 'FIM DE JOGO' },
  catchup: { en: 'WHILE YOU WERE AWAY', es: 'MIENTRAS NO ESTABAS', fr: 'PENDANT TON ABSENCE', pt: 'ENQUANTO VOCÊ SAIU' },
}

function introLine(r: Rundown, lang: Lang) {
  const n = r.segments.length
  if (r.phase === 'catchup' && r.window) {
    const a = Math.floor(r.window.from_s / 60) + 1, b = Math.floor(r.window.to_s / 60) + 1
    return { en: `What you missed, ${a}' to ${b}'`, es: `Lo que te perdiste, del ${a}' al ${b}'`,
      fr: `Ce que tu as raté, de la ${a}e à la ${b}e`, pt: `O que você perdeu, do ${a}' ao ${b}'` }[lang]
  }
  const ft = r.phase === 'ft'
  return {
    en: `${n} things that decided the ${ft ? 'second' : 'first'} half`,
    es: `${n} claves del ${ft ? 'segundo' : 'primer'} tiempo`,
    fr: `${n} clés de la ${ft ? 'seconde' : 'première'} période`,
    pt: `${n} chaves do ${ft ? 'segundo' : 'primeiro'} tempo`,
  }[lang]
}

function Studio({ rundown, meta, numbers, fan, back }: { rundown: Rundown; meta: Meta; numbers: number[]; fan: Profile; back: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ seg: -1, t: 0 })
  const [playing, setPlaying] = useState(true)
  const playRef = useRef(playing); playRef.current = playing
  const intro = rundown.intro.d
  const segs = rundown.segments
  const total = intro + segs.reduce((a, s) => a + s.duration_s, 0)
  const tRef = useRef(0)
  const audios = useMemo(() => segs.map(s => s.voice ? new Audio(s.voice.url) : null), [segs])
  useEffect(() => () => audios.forEach(a => a?.pause()), [audios])
  const lang = (rundown.lang as Lang) in LANG_NAME ? rundown.lang as Lang : 'en'
  const phase: Phase = rundown.phase ?? 'ht'

  useEffect(() => {
    const root = ref.current!
    let p: Pitch | null = null, st: Stadium | null = null
    if (GL) st = new Stadium(root)
    else { const c = document.createElement('canvas'); c.className = 'pitch-flat'; root.append(c); p = new Pitch(c) }
    const surface = () => (st ? st.hud : p!)
    if (p) p.colours = [meta.home.colour, meta.away.colour]
    if (st) { st.colours = [meta.home.colour, meta.away.colour]; st.inks = ['#0b1a12', '#ffffff']; st.mode = 'studio' }
    let raf = 0, last = performance.now(), first = true
    const onResize = () => { p?.resize(); st?.resize() }
    window.addEventListener('resize', onResize)
    const draw = (now: number) => {
      const dt = (now - last) / 1000; last = now
      if (playRef.current) tRef.current = Math.min(total, tRef.current + dt)
      const theme = readTheme(root)
      if (p) p.theme = theme
      if (st) st.theme = theme
      let t = tRef.current
      if (t < intro) {
        if (st) {
          const f0 = segs[0]?.clip.frames[0] ?? null
          st.begin(0.7)
          if (f0) st.setFrame(f0, null, numbers)
          st.render(f0, first); first = false
        } else p!.drawPitch(0.6)
        const sf = surface(), g = sf.ctx, s = sf.scale
        const ink = st ? '#f2f0e8' : sf.theme.text, muted = st ? '#b9c4bc' : sf.theme.muted
        g.fillStyle = sf.theme.accent; g.font = `800 ${s * 3}px 'Barlow Condensed'`; g.textAlign = 'center'; g.textBaseline = 'middle'
        g.fillText(TITLE[phase][lang], sf.sx(52.5), sf.sy(22))
        g.fillStyle = ink; g.font = `800 ${s * 9}px 'Barlow Condensed'`
        g.fillText(`${meta.home.short.toUpperCase()}  ${rundown.score.replace('-', ' – ')}  ${meta.away.short.toUpperCase()}`, sf.sx(52.5), sf.sy(32))
        g.fillStyle = muted; g.font = `500 ${s * 2.6}px Barlow`
        g.fillText(introLine(rundown, lang), sf.sx(52.5), sf.sy(41))
        g.fillStyle = sf.theme.accent; g.font = `600 ${s * 2.2}px Barlow`
        g.fillText(`Made for: ${fanLabel(fan, meta)}`, sf.sx(52.5), sf.sy(47))
        setPos(x => x.seg === -1 ? x : { seg: -1, t })
        follow(audios, segs, -1, 0)
      } else {
        t -= intro
        let k = 0
        while (k < segs.length - 1 && t >= segs[k].duration_s) { t -= segs[k].duration_s; k++ }
        const seg = segs[k]
        const tt = Math.min(t, seg.duration_s - 0.001)
        const clockLabel = `${meta.home.code} ${rundown.score} ${meta.away.code}  ·  ${Math.floor(seg.clip.t0 / 60) + 1}'`
        if (st) renderSegment3D(st, seg, tt, numbers, clockLabel)
        else renderSegment(p!, seg, tt, numbers, [], clockLabel)
        follow(audios, segs, playRef.current ? k : -1, tt)
        const a = audios[k]  // read-only probe for the sync check in headless runs
        ;(window as unknown as { __studio?: object }).__studio = {
          k, t: tt, voice: a && !a.paused ? a.currentTime + seg.voice!.start : null }
        setPos(x => (x.seg === k && Math.abs(x.t - tt) < 0.1) ? x : { seg: k, t: tt })
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize); st?.dispose(); root.replaceChildren() }
  }, [rundown, meta, numbers, intro, segs, total, audios, lang, phase, fan])

  const seg = pos.seg >= 0 ? segs[pos.seg] : null
  const line = seg?.script.find(l => pos.t >= l.t && pos.t < l.t + l.d + 0.3)
  const said = line && seg?.voice ? spokenTokens(seg.voice, line, pos.t) : null
  const elapsed = pos.seg < 0 ? pos.t : intro + segs.slice(0, pos.seg).reduce((a, s) => a + s.duration_s, 0) + pos.t
  const jump = (k: number) => { tRef.current = k < 0 ? 0 : intro + segs.slice(0, k).reduce((a, s) => a + s.duration_s, 0); setPlaying(true) }

  return <main className="studio">
    <div className="studio-frame">
      <div ref={ref} className="pitch studio-canvas stage3d" role="img" aria-label="Analysis segment" />
      <div className="captions" aria-live="polite">{line && said !== null
        ? line.text.split(/\s+/).map((w, i) => <span key={i} className={i < said ? 'said' : ''}>{w} </span>)
        : line?.text ?? ''}</div>
    </div>
    <div className="transport">
      <button onClick={back} className="ghost">◂ {phase === 'catchup' ? 'Back to the match' : 'Desk'}</button>
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
