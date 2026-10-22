import { useCallback, useEffect, useState } from 'react'
import type { Fact, FeedItem, Rundown, Segment, TrailItem } from './types'

interface Club { code: string; short: string; colour: string }
interface Stream { name: string; messages: number; lag_ms_first?: number | null; lag_ms_p50: number | null; lag_ms_p95: number | null }
interface RunSummary {
  runId: string; seed: number; status: string; error?: string | null; started_at: number
  whistle_at: number | null; locked_at: number | null; score: [number, number]; clubs: Club[]
  crew: { mode: string; model: string | null; killed: string[] }; stream: Stream; live: boolean
  clock?: number; feed?: FeedItem[]
}
interface Detail { live: boolean; now: number; run: RunSummary; trail: TrailItem[]; rundowns: Record<string, Rundown> }
interface AgentScore { jobs: number; first_try: number; after_retry: number; routed_around: number }
interface EvalSummary { agents: Record<string, AgentScore>; lock_median_s: number; lock_max_s: number; rundowns: unknown[] }

const AGENTS = ['analyst', 'producer', 'telestrator', 'writer']
const CUT_LABEL: Record<string, string> = { neutral: 'Neutral', home: 'Home fans', away: 'Away fans' }
const STATUS_LABEL: Record<string, string> = {
  first_half: 'First half', half_time: 'Half-time', second_half: 'Second half', full_time: 'Full time', failed: 'Failed',
}

function ago(t: number, now: number) {
  const s = Math.max(0, now - t)
  if (s < 90) return `${Math.round(s)} s ago`
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 129600) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

/** A replay that never reached full time stopped when it left the server (a restart or a deploy). */
function statusText(r: RunSummary, live: boolean) {
  const s = STATUS_LABEL[r.status] ?? r.status
  if (live || r.status === 'full_time' || r.status === 'failed') return s
  if (r.status === 'half_time') return 'stopped at half-time'
  return `stopped in the ${s.toLowerCase()}${r.clock ? ` at ${Math.floor(r.clock / 60)}'` : ''}`
}

function matchLine(r: RunSummary) {
  const [h, a] = r.clubs
  return `${h.short} ${r.score[0]}–${r.score[1]} ${a.short}`
}

async function post(url: string, body?: unknown) {
  const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `${url} failed (${r.status})`)
  return r.json()
}

export default function Proof() {
  const [list, setList] = useState<{ store: string; error: string | null; now: number; runs: RunSummary[] } | null>(null)
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get('run'))
  const [detail, setDetail] = useState<Detail | null>(null)
  const [evals, setEvals] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const loadList = useCallback(async () => {
    const r = await fetch('/api/proof/runs')
    if (r.ok) setList(await r.json())
    else setError(`Could not list runs (${r.status})`)
  }, [])

  useEffect(() => { loadList(); fetch('/api/proof/evals').then(r => r.ok ? r.json() : null).then(setEvals) }, [loadList])

  useEffect(() => {
    if (!selected && list?.runs.length) setSelected(list.runs[0].runId)
  }, [list, selected])

  useEffect(() => {
    if (!selected) return
    let stop = false, timer = 0
    history.replaceState(null, '', `/proof?run=${selected}`)
    const tick = async () => {
      const r = await fetch(`/api/proof/runs/${selected}`)
      if (stop) return
      if (!r.ok) { setError(r.status === 404 ? 'That run is not in memory or in the archive.' : `Could not load the run (${r.status})`); return }
      const d: Detail = await r.json()
      setDetail(d)
      if (d.live) loadList()  // keep the list's status in step with the run
      // a live run keeps changing until its rundown is locked and the half-time work is done
      if (d.live && d.run.status !== 'full_time' && d.run.status !== 'failed') timer = window.setTimeout(tick, 2000)
    }
    setDetail(null); setError(null); tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [selected, loadList])

  const killRun = async () => {
    setStarting(true); setError(null)
    try {
      const snap = await post('/api/runs', { speed: 8 })
      await post(`/api/runs/${snap.id}/crew/kill`, { role: 'telestrator' })
      await post(`/api/runs/${snap.id}/skip`)
      await loadList()
      setSelected(snap.id)
    } catch (e) { setError((e as Error).message) } finally { setStarting(false) }
  }

  const kill = async (role: string) => {
    if (!detail) return
    try { await post(`/api/runs/${detail.run.runId}/crew/kill`, { role }) } catch (e) { setError((e as Error).message) }
  }

  return (
    <div className="app proof">
      <header className="top">
        <a className="mark" href="/">RUN<span>DOWN</span></a>
        <nav className="nav"><a href="/">Live studio</a><a href="/proof" aria-current="page">Proof</a></nav>
      </header>
      {error && <div className="error" role="alert">{error}</div>}

      <section className="proof-intro">
        <h1>Proof</h1>
        <p>Every run the studio has made: what the agents did, when, and the match events behind every number on air.
          A <b>live</b> run is in progress on this server. A <b>replay</b> is read back from Cosmos DB.</p>
      </section>

      <div className="proof-grid">
        <aside className="run-list">
          <button className="go" onClick={killRun} disabled={starting}>{starting ? 'Starting…' : 'Kill the Telestrator ▸'}</button>
          <p className="muted small">Starts a fresh match with the Telestrator switched off and skips to half-time.
            Watch the crew route around it in the trail.</p>
          <h3>Runs</h3>
          {list?.error && <p className="warn small">The archive could not be read: {list.error}</p>}
          <ul>
            {list?.runs.length === 0 && <li className="muted small">No runs yet. Start one from the live studio.</li>}
            {list?.runs.map(r => <li key={r.runId}>
              <button className={`run-item ${selected === r.runId ? 'on' : ''}`} onClick={() => setSelected(r.runId)}>
                <span className={`tag ${r.live ? 'tag-live' : ''}`}>{r.live ? 'Live' : 'Replay'}</span>
                <b>{matchLine(r)}</b>
                <span className="muted small">Seed {r.seed} · {statusText(r, r.live)} · {ago(r.started_at, list.now)}</span>
              </button>
            </li>)}
          </ul>
          {list && <p className="muted small">Store: {list.store === 'cosmos' ? 'Azure Cosmos DB' : 'memory only (no archive configured)'}</p>}
        </aside>

        <main className="run-detail">
          {!detail && !error && <p className="muted">Loading…</p>}
          {detail && <RunDetail d={detail} kill={kill} />}
        </main>
      </div>

      {evals && <Evals evals={evals} />}
    </div>
  )
}

function RunDetail({ d, kill }: { d: Detail; kill: (role: string) => void }) {
  const r = d.run
  const cuts = Object.keys(d.rundowns)
  const [cut, setCut] = useState(cuts[0])
  useEffect(() => { if (!cut || !d.rundowns[cut]) setCut(Object.keys(d.rundowns)[0]) }, [d, cut])
  const lockAfter = r.whistle_at && r.locked_at ? r.locked_at - r.whistle_at : null
  const rundown = cut ? d.rundowns[cut] : undefined
  const canKill = d.live && r.status === 'first_half' && r.crew.mode === 'agents'
  return <>
    <div className="detail-head">
      <h2>{matchLine(r)}</h2>
      <span className={`tag ${d.live ? 'tag-live' : ''}`}>{d.live ? 'Live' : 'Replay'}</span>
      <span className="muted">Seed {r.seed} · {statusText(r, d.live)}</span>
    </div>
    {r.error && <div className="error" role="alert">{r.error}</div>}

    <dl className="facts-row">
      <div><dt>Rundown locked</dt><dd>{lockAfter !== null ? <><b>{lockAfter.toFixed(2)} s</b> after the whistle</> : 'not yet'}</dd></div>
      <div><dt>Match stream</dt><dd><b>{r.stream.name === 'eventhubs' ? 'Azure Event Hubs' : r.stream.name}</b>
        {r.stream.messages > 0 && <> · {r.stream.messages} messages · in transit {r.stream.lag_ms_p50} ms median, {r.stream.lag_ms_p95} ms p95{r.stream.lag_ms_first !== null && r.stream.lag_ms_first !== undefined && <>, first message {r.stream.lag_ms_first} ms</>}</>}</dd></div>
      <div><dt>Crew</dt><dd><b>{r.crew.mode === 'agents' ? `Agents on ${r.crew.model}` : 'Rules crew'}</b>
        {r.crew.killed.length > 0 && <> · switched off: {r.crew.killed.join(', ')}</>}</dd></div>
    </dl>
    {r.whistle_at && <p className="muted small">Whistle heard {new Date(r.whistle_at * 1000).toLocaleTimeString()},
      rundown locked {r.locked_at ? new Date(r.locked_at * 1000).toLocaleTimeString() : '…'} (server clock).</p>}
    {canKill && !r.crew.killed.includes('telestrator') &&
      <button className="ghost" onClick={() => kill('telestrator')}>Switch off the Telestrator now</button>}

    <h3>On air</h3>
    {!rundown && <p className="muted small">{r.status === 'first_half' ? 'The rundown locks at the half-time whistle.' : 'No rundown was saved for this run.'}</p>}
    {rundown && <>
      {cuts.length > 1 && <div className="toggles" role="group" aria-label="Cut">
        {cuts.map(k => { const [c, l] = k.split('|'); return <button key={k} className={k === cut ? 'on' : ''} onClick={() => setCut(k)}>{CUT_LABEL[c] ?? c} · {l.toUpperCase()}</button> })}
      </div>}
      <ol className="proof-segs">{rundown.segments.map((s, i) => <ProofSegment key={s.segment_id + i} s={s} i={i} rulesOnly={rundown.made_by !== 'crew'} />)}</ol>
      <p className="muted small">{rundown.duration_s.toFixed(1)} s for a {rundown.slot_s} s slot.</p>
    </>}

    <h3>Trail <span className="muted small">({d.trail.length} entries: every agent call, retry, fallback and Verifier check)</span></h3>
    <Trail trail={d.trail} t0={r.started_at} />
  </>
}

function ProofSegment({ s, i, rulesOnly }: { s: Segment; i: number; rulesOnly: boolean }) {
  const crew = s.provenance.crew
  return <li className="proof-seg">
    <div className="seg-top">
      <span className="slot">{i + 1}</span>
      <div>
        <b className="seg-name">{s.title}</b>
        <span className="muted small"> {s.duration_s.toFixed(1)} s · clip from {Math.floor(s.clip.t0 / 60)}'</span>
        <p className="small made">
          {rulesOnly
            ? <span>Rules crew: every number read straight from the stats engine</span>
            : <span className={s.provenance.verified ? 'ok' : 'warn'}>{s.provenance.verified ? '✓ Verified' : '✗ Not verified'}</span>}
          {crew && <> · picture by the {crew.telestrator === 'agent' ? 'Telestrator agent' : 'rules crew'} · words by the {crew.writer === 'agent' ? 'Writer agent' : 'rules crew'}</>}
        </p>
        {s.provenance.why && <p className="why small"><b>Analyst:</b> {s.provenance.why}</p>}
        <ul className="fact-list">{s.facts.map(f => <FactRow key={f.key} f={f} />)}</ul>
      </div>
    </div>
  </li>
}

function FactRow({ f }: { f: Fact }) {
  return <li>
    <b>{String(f.value)} {f.unit}</b> {f.label}
    <code>{f.key}{f.evidence.length > 0 && <> ← {f.evidence.slice(0, 10).join(' ')}{f.evidence.length > 10 && ` +${f.evidence.length - 10}`}</>}</code>
  </li>
}

function Trail({ trail, t0 }: { trail: TrailItem[]; t0: number }) {
  const [agent, setAgent] = useState('all')
  const agents = ['all', ...Array.from(new Set(trail.map(t => t.agent)))]
  const rows = trail.filter(t => agent === 'all' || t.agent === agent)
  if (trail.length === 0) return <p className="muted small">No trail entries. The rules crew writes none; agent runs log every step.</p>
  return <>
    <div className="toggles" role="group" aria-label="Agent">
      {agents.map(a => <button key={a} className={a === agent ? 'on' : ''} onClick={() => setAgent(a)}>{a === 'all' ? 'All' : a}</button>)}
    </div>
    <div className="trail-wrap">
      <table className="trail">
        <thead><tr><th>#</th><th>At</th><th>Agent</th><th>What</th><th>Status</th><th>Took</th><th>Why</th><th>Events</th></tr></thead>
        <tbody>{rows.map(t => <tr key={t.seq} className={`st-${t.status}`}>
          <td>{t.seq}</td>
          <td>+{(t.at - t0).toFixed(1)} s</td>
          <td>{t.agent}</td>
          <td>{t.action}</td>
          <td><span className="st">{t.status}</span></td>
          <td>{t.ms !== null ? `${(t.ms / 1000).toFixed(1)} s` : ''}</td>
          <td className="why-cell">{t.why}</td>
          <td title={t.event_ids.join(' ')}>{t.event_ids.length || ''}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </>
}

interface SystemScore {
  rundowns: number; segments: number; stories_aired: number; relevant: number; grounded: number; facts_checked: number
  slot_error_median_s: number; slot_error_max_s: number; lock_median_s?: number; lock_max_s?: number
  rejected?: number; routed_around?: number
}
interface HalftimeSummary {
  seeds: number[]; stories: number; spotted: number; spotted_delay_median_min: number | null
  agents?: SystemScore; rules: SystemScore
}
interface Judged { segments: number; scored: number; mean: number | null; at_least_4: number }

const pct = (a: number, b: number) => b ? `${a}/${b} (${Math.round(100 * a / b)}%)` : '–'

function Evals({ evals }: { evals: Record<string, unknown> }) {
  const half = evals['rundown'] as HalftimeSummary | undefined
  const first = evals['rundown-first'] as HalftimeSummary | undefined
  const runs = [first, evals['rundown-second'] as HalftimeSummary | undefined, half]
    .filter((x): x is HalftimeSummary => !!x?.agents)
  const judged = evals['foundry-groundedness'] as Record<string, Judged> | undefined
  const crew = (['crew-reliability', 'crew-kill-telestrator'] as const).filter(k => evals[k])
  const titles: Record<string, string> = {
    'crew-reliability': 'All agents on (8 rundowns, 4 recorded matches)',
    'crew-kill-telestrator': 'Telestrator switched off (4 rundowns, 2 matches)',
  }
  const rows: [string, (x: SystemScore) => string][] = half ? [
    ['Story recall: planted stories on air', x => pct(x.stories_aired, half.stories)],
    ['Precision: aired points that are a planted story or a real goal or chance', x => pct(x.relevant, x.segments)],
    ['Grounding: segments whose every fact the Verifier recomputes', x => pct(x.grounded, x.segments)],
    ['Slot fit: distance from 90 s (median, worst)', x => `${x.slot_error_median_s} s, ${x.slot_error_max_s} s`],
  ] : []
  return <section className="evals">
    <h2>Evals</h2>
    {half && <>
      <h3>Half-time rundowns against the answer key</h3>
      <p className="small">{half.seeds.length} fresh seeds ({half.seeds[0]} to {half.seeds[half.seeds.length - 1]}), neutral English, 90 s slot.
        Each match plants tactical stories the agents never see. The detectors spotted <b>{pct(half.spotted, half.stories)}</b> of
        the first-half ones by half-time, a median {half.spotted_delay_median_min} min after they started.
        Both systems then choose from the same talking points.</p>
      <div className="trail-wrap"><table className="trail eval">
        <thead><tr><th></th><th>Agent crew</th><th>Rules-only baseline</th></tr></thead>
        <tbody>
          {rows.map(([label, f]) => <tr key={label}><td>{label}</td><td>{half.agents ? f(half.agents) : '–'}</td><td>{f(half.rules)}</td></tr>)}
          {half.agents && <tr><td>Locked after the whistle (median, worst)</td><td>{half.agents.lock_median_s} s, {half.agents.lock_max_s} s</td><td>at once</td></tr>}
          {half.agents && <tr><td>Answers the Verifier rejected before air</td><td>{half.agents.rejected} ({half.agents.routed_around} routed to the rules crew)</td><td>–</td></tr>}
          {judged && <tr><td>Foundry groundedness judge: scripts scoring 4 or 5 of 5</td>
            <td>{judged.agents ? `${pct(judged.agents.at_least_4, judged.agents.scored)}, mean ${judged.agents.mean}` : '–'}</td>
            <td>{judged.rules ? `${pct(judged.rules.at_least_4, judged.rules.scored)}, mean ${judged.rules.mean}` : '–'}</td></tr>}
        </tbody>
      </table></div>
      {runs.length > 1 && <p className="small">The agents' picks vary from run to run. Story recall over {runs.length} runs on the
        same seeds: <b>{runs.map(x => `${Math.round(100 * x.agents!.stories_aired / x.stories)}%`).join(', ')}</b>; the
        rules baseline is deterministic at {Math.round(100 * half.rules.stories_aired / half.stories)}%.</p>}
      {first?.agents && <p className="muted small">First run, before its fix: the agents put nothing on air for one seed
        (slot fit worst {first.agents.slot_error_max_s} s), because a lone talking point was given the whole slot and broke the
        schema's 60 s segment limit. Segments are now capped at 60 s. Every run and every miss is in <code className="inline">docs/EVALS.md</code>.</p>}
    </>}
    {crew.length > 0 && <>
      <h3>How often each agent's answer passed the Verifier</h3>
      <p className="muted small">On gpt-4.1-mini. Anything that fails twice is routed around to the rules crew, so nothing unverified
        goes on air. Raw runs are in <code className="inline">docs/evals</code>.</p>
      <div className="eval-grid">
        {crew.map(k => { const e = evals[k] as EvalSummary; return <div key={k}>
          <h3>{titles[k]}</h3>
          <div className="trail-wrap"><table className="trail eval">
            <thead><tr><th>Agent</th><th>Jobs</th><th>First try</th><th>After one retry</th><th>Routed around</th></tr></thead>
            <tbody>{AGENTS.map(a => { const s = e.agents[a]; return <tr key={a}>
              <td>{a}</td><td>{s.jobs}</td><td>{s.first_try}</td><td>{s.after_retry}</td><td>{s.routed_around}</td></tr> })}</tbody>
          </table></div>
          <p className="small">Rundown locked <b>{e.lock_median_s} s</b> after the whistle (median), {e.lock_max_s} s at worst.</p>
        </div> })}
      </div>
    </>}
  </section>
}
