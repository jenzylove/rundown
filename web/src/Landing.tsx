import { useEffect, useRef, useState } from 'react'
import { readTheme, renderSegment3D, bug } from './renderer'
import { inkFor, Stadium, webglAvailable } from './stadium'
import type { Meta, Rundown } from './types'

interface Showcase { seed: number; meta: Meta; cuts: Record<string, Rundown> }

const GH = 'https://github.com/jenzylove/rundown'
const FANS: { key: string; label: string; sub: string }[] = [
  { key: 'neutral|en|analyst|-', label: 'Neutral analyst', sub: 'English · the numbers behind it' },
  { key: 'away|fr|casual', label: 'Eastmoor fan', sub: 'Français · plain words · follows a midfielder' },
]

/** The hero: a real segment from a fixed first half, played in the stadium, for the fan you pick. */
function HeroStage({ show, fanKey, sound }: { show: Showcase | null; fanKey: string; sound: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const [line, setLine] = useState('')
  const [title, setTitle] = useState('')
  useEffect(() => {
    const root = ref.current!
    if (!webglAvailable()) return
    const st = new Stadium(root)
    st.mode = 'studio'
    if (window.innerWidth > 900) { st.shiftX = 0.2; st.hudX0 = 0.44; st.resize() }
    let raf = 0, t = 0, last = performance.now(), k = 0, first = true
    const cut = show ? Object.entries(show.cuts).find(([key]) => key.startsWith(fanKey))?.[1] ?? null : null
    const audios = (cut?.segments ?? []).map(sg => sound && sg.voice ? new Audio(sg.voice.url) : null)
    const numbers = show ? [...show.meta.home.starters, ...show.meta.away.starters].map(id =>
      [...show.meta.home.players, ...show.meta.away.players].find(p => p.id === id)?.number ?? 0) : null
    if (show) { st.colours = [show.meta.home.colour, show.meta.away.colour]; st.inks = [inkFor(show.meta.home.colour), inkFor(show.meta.away.colour)] }
    const onResize = () => st.resize()
    window.addEventListener('resize', onResize)
    const draw = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000); last = now
      st.theme = readTheme(root)
      const segs = cut?.segments ?? []
      if (segs.length && show) {
        t += dt
        if (t >= segs[k].duration_s) { t = 0; k = (k + 1) % segs.length }
        const seg = segs[k]
        audios.forEach((a, i) => {  // the voice of the segment on screen, in step with it
          if (!a) return
          const v = segs[i].voice!, at = t - v.start
          if (i !== k || at < 0 || at > v.audio_s) { if (!a.paused) a.pause(); return }
          if (a.paused) { a.currentTime = at; a.play().catch(() => {}) } else if (Math.abs(a.currentTime - at) > 0.25) a.currentTime = at
        })
        renderSegment3D(st, seg, t, numbers ?? [], `${show.meta.home.code} ${cut!.score} ${show.meta.away.code}  ·  ${Math.floor(seg.clip.t0 / 60) + 1}'`)
        const ln = seg.script.find(l => t >= l.t && t < l.t + l.d + 0.3)
        setLine(x => x === (ln?.text ?? '') ? x : ln?.text ?? '')
        setTitle(x => x === seg.title ? x : seg.title)
      } else {
        st.mode = 'hero'
        st.begin(0)
        bug(st.hud, 'HAR 0-0 EMA', false)
        st.render(null, first); first = false
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize); audios.forEach(a => a?.pause()); st.dispose(); root.replaceChildren() }
  }, [show, fanKey, sound])
  return <div className="hero-stage">
    <div ref={ref} className="stage3d hero-canvas" role="img" aria-label={`A half-time segment: ${title}`} />
    {line && <div className="hero-caption" aria-live="polite">{line}</div>}
  </div>
}

export default function Landing() {
  const [show, setShow] = useState<Showcase | null>(null)
  const [fan, setFan] = useState(FANS[1].key)
  const [sound, setSound] = useState(false)
  useEffect(() => {
    let gone = false
    const load = (n: number) => fetch('/api/showcase').then(r => r.ok ? r.json() : Promise.reject())
      .then(d => { if (!gone) setShow(d) }).catch(() => { if (!gone && n < 6) setTimeout(() => load(n + 1), 4000) })
    load(0)
    return () => { gone = true }
  }, [])

  return <div className="landing">
    <header className="lnav">
      <a className="mark" href="/">RUN<span>DOWN</span></a>
      <nav>
        <a href="#how">How it works</a>
        <a href="#proof">Proof</a>
        <a href="#same">Two fans</a>
        <a href="#faq">FAQ</a>
        <a href={GH}>GitHub</a>
      </nav>
      <a className="btn" href="/watch">Make my show ▸</a>
    </header>

    <section className="hero">
      <HeroStage show={show} fanKey={fan} sound={sound} />
      <div className="hero-copy">
        <p className="eyebrow">Rundown For You</p>
        <h1>Every fan's own<br />half-time show.</h1>
        <p className="lede">Your club, your player, your language, your depth. A crew of AI agents makes it while the
          first half plays, and every claim is checked before it airs. Ready at the whistle.</p>
        <div className="cta">
          <a className="btn big" href="/watch">Make my show ▸</a>
          <a className="btn ghost big" href="/proof">See the proof</a>
        </div>
      </div>
      <div className="hero-switch" role="group" aria-label="Whose show to play">
        <span className="hs-label">Same match, made for</span>
        {FANS.map(f => <button key={f.key} className={fan === f.key ? 'on' : ''} aria-pressed={fan === f.key} onClick={() => setFan(f.key)}>
          <b>{f.label}</b><span>{f.sub}</span></button>)}
        <button className={`hs-sound ${sound ? 'on' : ''}`} aria-pressed={sound} onClick={() => setSound(x => !x)}>
          <b>{sound ? '🔊 Sound on' : '🔈 Play with sound'}</b><span>{sound ? 'the voice-over, as a fan hears it' : 'hear the show in that fan\'s language'}</span></button>
        <span className="hs-note">{show ? 'A segment from one showcase match, drawn from its tracking and checked by the Verifier. Press Make my show for a fresh match.' : 'Loading a real segment…'}</span>
      </div>
    </section>

    <section className="band" id="problem">
      <p className="num">01</p>
      <div>
        <h2>Half time is made once.</h2>
        <p className="big-quote">One studio, one market, one language. A fan of the away side in Lisbon, a casual viewer in
          Lyon and an analyst in Lagos all get the same three segments, or none at all.</p>
        <div className="facts">
          <p><b>Personalized shows exist.</b> But people still check every script before it goes out
            (<a href="https://www.espnfrontrow.com/2025/08/chris-fowler-us-open-sportscenter-for-you/">ESPN on SportsCenter For You</a>),
            so they come once a day, never live.</p>
          <p><b>So the hard part isn't writing.</b> It's checking. A machine that recomputes every claim before it
            airs is what makes a live show for every fan possible.</p>
        </div>
      </div>
    </section>

    <section className="band" id="how">
      <p className="num">02</p>
      <div>
        <h2>How your show gets made</h2>
        <ol className="steps">
          <li><span>1</span><div><h3>Say who you are</h3><p>Pick your side, casual or analyst, English, Español, Français or
            Português, and a player to follow. Ten seconds, no sign up.</p></div></li>
          <li><span>2</span><div><h3>The crew watches the half</h3><p>The match streams live through Azure Event Hubs. Code
            computes every stat. Agents on Microsoft Agent Framework spot the stories, judge what matters to <i>your</i> club, plan the
            picture and write the words.</p></div></li>
          <li><span>3</span><div><h3>The Verifier checks every claim</h3><p>Before anything airs, code recomputes every number,
            checks it was said the right way round (a rise is a rise), and that a casual cut stays plain. A segment that fails is
            retried, rebuilt or dropped.</p></div></li>
          <li><span>4</span><div><h3>Your show at the whistle</h3><p>Three segments: the stories of your half, drawn on the pitch,
            voiced by Azure AI Speech with captions that follow every word, and one on your player. Missed a spell? Press
            Catch me up.</p></div></li>
        </ol>
      </div>
    </section>

    <section className="band" id="proof">
      <p className="num">03</p>
      <div>
        <h2>Measured, not claimed</h2>
        <p className="sub">Twelve matches the system was never tuned on, eight fans each, the real agents.</p>
        <div className="stats">
          <div><b>274 / 274</b><span>segments passed the Verifier and aired</span></div>
          <div><b>36 / 36</b><span>casual and analyst shows that read differently</span></div>
          <div><b>24 / 24</b><span>followed players with their own segment</span></div>
          <div><b>~12 s</b><span>to make one fan's show after the whistle</span></div>
        </div>
        <h3 className="chart-title">Errors we planted in copies of aired segments, caught before air</h3>
        <div className="bars">
          {[['A number changed in a spoken line', 95], ['A before and now pair the wrong way round', 97], ['A rise said as a fall', 85], ['A number changed on the stat card', 95]].map(([k, v]) =>
            <div className="bar" key={k as string}><span>{k}</span><div className="track"><i className="new" style={{ width: `${v}%` }} /></div><b>0% → {v}%</b></div>)}
        </div>
        <p className="foot">0% is the Verifier as it was on 22 October, which checked the facts but not what was said about them.
          Every miss is published: <a href={`${GH}/blob/main/docs/EVALS.md`}>docs/EVALS.md</a>. Every live run's trail: <a href="/proof">Proof</a>.</p>
      </div>
    </section>

    <section className="band" id="same">
      <p className="num">04</p>
      <div>
        <h2>Same moment, two different shows</h2>
        <p className="sub">The first segment of the showcase match, as two fans get it. Same goal, same numbers underneath.</p>
        {show ? <div className="compare">
          {FANS.map(f => {
            const cut = Object.entries(show.cuts).find(([key]) => key.startsWith(f.key))?.[1]
            const seg = cut?.segments[0]
            return <div key={f.key} className="said">
              <p className="who"><b>{f.label}</b><span>{f.sub}</span></p>
              {seg && <><h3>{seg.title}</h3>{seg.script.map((l, i) => <p key={i} className="line">“{l.text}”</p>)}</>}
            </div>
          })}
        </div> : <p className="sub">Loading…</p>}
        <p className="foot">Broadcasters get the same crew with a producer's desk (approve, swap, drop) and a timed overlay file for
          their own graphics; other apps can ask for a fan's show over MCP. Details in the <a href={GH}>README</a>.</p>
      </div>
    </section>

    <section className="band" id="faq">
      <p className="num">05</p>
      <div>
        <h2>Questions</h2>
        <details><summary>Is this real match data?</summary><p>No. Every club, player and match is synthetic, made by our own
          simulator: 22 players and the ball tracked ten times a second, with tactical stories planted in each match so we can
          score whether the crew finds them.</p></details>
        <details><summary>Does a person check each fan's show?</summary><p>No, that's the point. The Verifier checks every segment
          in code before it airs. The studio's own master rundown still has a human desk.</p></details>
        <details><summary>What doesn't the Verifier check?</summary><p>Numbers, their order, their direction and the depth are
          checked. Descriptive colour, like how a goal was scored, isn't; Foundry's groundedness judge watches that.</p></details>
        <details><summary>Which languages?</summary><p>English, Spanish, French and Portuguese, written and voiced.</p></details>
        <details><summary>Can I run it myself?</summary><p>Yes. It's open source under MIT; without Azure keys it runs with the
          rules crew. See the <a href={GH}>README</a>.</p></details>
      </div>
    </section>

    <footer className="lfoot">
      <div><a className="mark" href="/">RUN<span>DOWN</span></a><p>Every fan's own half-time show.</p></div>
      <nav>
        <a href="/watch">Make my show</a><a href="/proof">Proof</a><a href={GH}>GitHub</a>
        <a href={`${GH}/blob/main/docs/EVALS.md`}>Evals</a>
      </nav>
      <p className="built">Built on Microsoft Foundry · Microsoft Agent Framework · Azure AI Speech · Azure Event Hubs · Azure Container Apps · Azure Cosmos DB</p>
      <p className="fine">Built for the Microsoft and Premier League Inside the Game Developer Hackathon. Synthetic data,
        fictional clubs. MIT licensed.</p>
    </footer>
  </div>
}
