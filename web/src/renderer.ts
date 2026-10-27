// Canvas renderer: the live pitch, and Overlay Script v1 segment playback.
import type { Cue, Segment } from './types'
import type { Stadium, Surface } from './stadium'

export interface Theme {
  bg: string; pitch: string; stripe: string; line: string; text: string; muted: string
  accent: string; accentInk: string; panel: string; dim: string; shadow: string
  gfxInk: string; gfxMuted: string; outline: string
}

export function readTheme(el: HTMLElement): Theme {
  const s = getComputedStyle(el)
  const v = (n: string) => s.getPropertyValue(n).trim()
  return {
    bg: v('--pitch-bg'), pitch: v('--pitch'), stripe: v('--pitch-stripe'), line: v('--pitch-line'),
    text: v('--ink'), muted: v('--ink-muted'), accent: v('--accent'), accentInk: v('--accent-ink'),
    panel: v('--gfx-panel'), dim: v('--pitch-dim'), shadow: v('--pitch-shadow'),
    gfxInk: v('--gfx-ink'), gfxMuted: v('--gfx-muted'), outline: v('--home-outline'),
  }
}

const LEN = 105, WID = 68
const DISPLAY = "'Barlow Condensed', 'Arial Narrow', sans-serif"
const BODY = "Barlow, system-ui, sans-serif"

export class Pitch {
  ctx: CanvasRenderingContext2D
  w = 0; h = 0; m = 0; scale = 1
  theme!: Theme
  colours: [string, string] = ['#f4f1ea', '#e5484d']
  inks: [string, string] = ['#0b1a12', '#ffffff']
  constructor(public canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext('2d')!
    this.resize()
  }
  resize() {
    const r = this.canvas.getBoundingClientRect()
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.canvas.width = Math.max(1, Math.round(r.width * dpr))
    this.canvas.height = Math.max(1, Math.round(r.height * dpr))
    this.w = this.canvas.width; this.h = this.canvas.height
    this.m = Math.max(this.w, this.h) * 0.035
    this.scale = Math.min((this.w - 2 * this.m) / LEN, (this.h - 2 * this.m) / WID)
  }
  get ox() { return (this.w - LEN * this.scale) / 2 }
  get oy() { return (this.h - WID * this.scale) / 2 }
  sx(x: number) { return this.ox + x * this.scale }
  sy(y: number) { return this.oy + y * this.scale }
  px(n: number) { return n * this.scale / 10 } // design unit: 1 = 1/10 m at this zoom

  drawPitch(dim = 0) {
    const g = this.ctx, t = this.theme
    g.fillStyle = t.bg; g.fillRect(0, 0, this.w, this.h)
    g.fillStyle = t.pitch; g.fillRect(this.sx(0), this.sy(0), LEN * this.scale, WID * this.scale)
    for (let i = 0; i < 14; i += 2) {
      g.fillStyle = t.stripe; g.fillRect(this.sx(i * LEN / 14), this.sy(0), LEN / 14 * this.scale, WID * this.scale)
    }
    g.strokeStyle = t.line; g.lineWidth = Math.max(1, this.scale * 0.14)
    g.strokeRect(this.sx(0), this.sy(0), LEN * this.scale, WID * this.scale)
    g.beginPath(); g.moveTo(this.sx(52.5), this.sy(0)); g.lineTo(this.sx(52.5), this.sy(WID)); g.stroke()
    g.beginPath(); g.arc(this.sx(52.5), this.sy(34), 9.15 * this.scale, 0, Math.PI * 2); g.stroke()
    for (const [x0, dir] of [[0, 1], [LEN, -1]] as const) {
      const box = (d: number, w: number) => g.strokeRect(Math.min(this.sx(x0), this.sx(x0 + dir * d)), this.sy(34 - w / 2), d * this.scale, w * this.scale)
      box(16.5, 40.32); box(5.5, 18.32)
      g.beginPath(); g.arc(this.sx(x0 + dir * 11), this.sy(34), this.scale * 0.25, 0, Math.PI * 2); g.fillStyle = t.line; g.fill()
    }
    if (dim) { g.fillStyle = t.dim; g.globalAlpha = dim; g.fillRect(0, 0, this.w, this.h); g.globalAlpha = 1 }
  }

  /** frame: [ball_x, ball_y, ball_z, 22 x (x, y)] */
  drawPlayers(f: number[], focus: Set<number> | null, numbers: number[] | null) {
    const g = this.ctx, r = this.scale * 0.95
    for (let s = 21; s >= 0; s--) {
      const x = this.sx(f[3 + 2 * s]), y = this.sy(f[4 + 2 * s]), side = s < 11 ? 0 : 1
      g.globalAlpha = focus && !focus.has(s) ? 0.42 : 1
      g.fillStyle = this.theme.shadow; g.beginPath(); g.ellipse(x + r * 0.25, y + r * 0.35, r, r * 0.6, 0, 0, Math.PI * 2); g.fill()
      g.fillStyle = this.colours[side]; g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill()
      if (s % 11 === 0) { g.strokeStyle = this.theme.accent; g.lineWidth = r * 0.18; g.stroke() }
      else if (side === 0) { g.strokeStyle = this.theme.outline; g.lineWidth = r * 0.12; g.stroke() }
      if (numbers) {
        g.fillStyle = this.inks[side]; g.font = `700 ${r * 1.05}px ${DISPLAY}`; g.textAlign = 'center'; g.textBaseline = 'middle'
        g.fillText(String(numbers[s]), x, y + r * 0.06)
      }
    }
    g.globalAlpha = 1
    // ball drawn above the receiver, offset so it never hides the shirt number
    const bx = this.sx(f[0]) + r * 0.7, by = this.sy(f[1]) - r * 0.7 - f[2] * this.scale * 0.35
    g.fillStyle = this.theme.shadow; g.beginPath(); g.arc(this.sx(f[0]) + r * 0.8, this.sy(f[1]) - r * 0.5, r * 0.35, 0, Math.PI * 2); g.fill()
    g.fillStyle = '#ffffff'; g.beginPath(); g.arc(bx, by, r * 0.42 + f[2] * 0.02 * this.scale, 0, Math.PI * 2); g.fill()
    g.strokeStyle = '#0b0f0d'; g.lineWidth = Math.max(1, r * 0.1); g.stroke()
  }
}

// ------------------------------------------------------------ segment playback

const ease = (u: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, u)), 3)

export function clipTime(seg: Segment, t: number): { clip: number; frozen: boolean } {
  let clip = t
  const fr = seg.cues.filter(q => q.kind === 'freeze').sort((a, b) => a.t - b.t)
  for (const f of fr) {
    if (t >= f.t + f.d) clip -= f.d
    else if (t >= f.t) return { clip: f.at ?? 0, frozen: true }
  }
  const len = (seg.clip.frames.length - 1) / seg.clip.hz
  return { clip: Math.min(clip, len), frozen: false }
}

export function sampleClip(seg: Segment, clipT: number): number[] {
  const F = seg.clip.frames, k = Math.max(0, Math.min(F.length - 1.0001, clipT * seg.clip.hz))
  const i = Math.floor(k), u = k - i, a = F[i], b = F[Math.min(i + 1, F.length - 1)]
  return a.map((v, j) => v + (b[j] - v) * u)
}

interface Box { x: number; y: number; w: number; h: number }
const hits = (b: Box, pts: [number, number][]) => pts.some(([x, y]) => x > b.x - 20 && x < b.x + b.w + 20 && y > b.y - 20 && y < b.y + b.h + 20)

export function renderSegment(p: Pitch, seg: Segment, t: number, numbers: number[], names: string[], clock: string) {
  const th = p.theme
  const { clip, frozen } = clipTime(seg, t)
  const f = sampleClip(seg, clip)
  const active = seg.cues.filter(q => t >= q.t && t < q.t + q.d)
  const focus = new Set(active.filter(q => q.kind === 'highlight' || q.kind === 'run').map(q => q.slot!))
  const freezeCue = active.find(q => q.kind === 'freeze')
  const dim = frozen && freezeCue ? 0.5 * ease((t - freezeCue.t) / 0.35) : 0
  p.drawPitch(dim)

  for (const q of active) {
    const u = ease((t - q.t) / Math.min(q.d, 0.9))
    if (q.kind === 'zone' && q.rect) drawZone(p, q, u, t)
    if (q.kind === 'line' && q.x !== undefined) drawLine(p, q, u)
  }
  p.drawPlayers(f, focus.size ? focus : null, numbers)
  for (const q of active) {
    const u = ease((t - q.t) / Math.min(q.d * 0.6, 0.9))
    if (q.kind === 'arrow' && q.from && q.to) arrow(p, p.sx(q.from[0]), p.sy(q.from[1]), p.sx(q.to[0]), p.sy(q.to[1]), u,
      q.style === 'pass' || q.style === 'carry' ? th.text : th.accent, q.style === 'carry', q.style === 'cross' ? 0.5 : q.style === 'shot' ? 0.05 : 0.15)
    if (q.kind === 'run' && q.slot !== undefined) {
      const pts: [number, number][] = []
      const n = 24, a = q.from_t ?? 0, b = q.to_t ?? a + 3
      for (let k = 0; k <= n * u; k++) {
        const fr = sampleClip(seg, a + (b - a) * k / n)
        pts.push([p.sx(fr[3 + 2 * q.slot]), p.sy(fr[4 + 2 * q.slot])])
      }
      polyline(p, pts, th.accent, true)
    }
  }
  const occupied: [number, number][] = [[p.sx(f[0]), p.sy(f[1])]]
  for (const q of active) {
    if (q.kind === 'highlight' && q.slot !== undefined) {
      const x = p.sx(f[3 + 2 * q.slot]), y = p.sy(f[4 + 2 * q.slot])
      occupied.push([x, y])
      ring(p, x, y, ease((t - q.t) / 0.5), t)
      if (q.text) tag(p, x, y, q.text, ease((t - q.t) / 0.4))
    }
  }
  for (const q of active) {
    if (q.kind === 'label') lowerThird(p, q, t, occupied)
    if (q.kind === 'statcard') statCard(p, q, t, occupied)
  }
  bug(p, clock, frozen)
  void names
}

function drawZone(p: Surface, q: Cue, u: number, t: number) {
  const g = p.ctx, [x, y, w, h] = q.rect!
  const pulse = 0.5 + 0.5 * Math.sin((t - q.t) * 4)
  g.save()
  g.globalAlpha = u * (0.16 + 0.08 * pulse); g.fillStyle = p.theme.accent
  g.fillRect(p.sx(x), p.sy(y), w * p.scale, h * p.scale)
  g.globalAlpha = u; g.strokeStyle = p.theme.accent; g.lineWidth = p.scale * 0.22; g.setLineDash([p.scale * 1.1, p.scale * 0.8])
  g.strokeRect(p.sx(x), p.sy(y), w * p.scale, h * p.scale); g.setLineDash([])
  if (q.text) {
    g.font = `800 ${p.scale * 4.2}px ${DISPLAY}`; g.fillStyle = p.theme.accent; g.textAlign = 'left'; g.textBaseline = 'top'
    g.fillText(q.text.toUpperCase(), p.sx(x) + p.scale, p.sy(y) + p.scale * 0.8)
  }
  g.restore()
}

function drawLine(p: Surface, q: Cue, u: number) {
  const g = p.ctx, x = p.sx(q.x!)
  g.save(); g.strokeStyle = p.theme.accent; g.lineWidth = p.scale * 0.35; g.shadowColor = p.theme.accent; g.shadowBlur = p.scale * 1.5
  g.beginPath(); g.moveTo(x, p.sy(0)); g.lineTo(x, p.sy(WID * u)); g.stroke(); g.shadowBlur = 0
  if (q.text) {
    g.font = `700 ${p.scale * 2.2}px ${DISPLAY}`; const w = g.measureText(q.text.toUpperCase()).width + p.scale * 1.6
    g.fillStyle = p.theme.accent; g.fillRect(x - w / 2, p.sy(1), w, p.scale * 3)
    g.fillStyle = p.theme.accentInk; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(q.text.toUpperCase(), x, p.sy(1) + p.scale * 1.55)
  }
  g.restore()
}

function arrow(p: Surface, x0: number, y0: number, x1: number, y1: number, u: number, col: string, dashed: boolean, bend: number) {
  const g = p.ctx
  const mx = (x0 + x1) / 2 + bend * (y1 - y0) * 0.5, my = (y0 + y1) / 2 - bend * (x1 - x0) * 0.5
  const pt = (v: number): [number, number] => [(1 - v) ** 2 * x0 + 2 * (1 - v) * v * mx + v * v * x1, (1 - v) ** 2 * y0 + 2 * (1 - v) * v * my + v * v * y1]
  const pts: [number, number][] = []
  for (let k = 0; k <= 40 * u; k++) pts.push(pt(k / 40))
  polyline(p, pts, col, dashed)
  if (u > 0.96) {
    const [ax, ay] = pt(0.94), ang = Math.atan2(y1 - ay, x1 - ax), L = p.scale * 2.2
    g.fillStyle = col; g.beginPath(); g.moveTo(x1, y1)
    g.lineTo(x1 - L * Math.cos(ang - 0.45), y1 - L * Math.sin(ang - 0.45)); g.lineTo(x1 - L * Math.cos(ang + 0.45), y1 - L * Math.sin(ang + 0.45)); g.fill()
  }
}

function polyline(p: Surface, pts: [number, number][], col: string, dashed: boolean) {
  if (pts.length < 2) return
  const g = p.ctx
  g.save(); g.strokeStyle = col; g.lineWidth = p.scale * 0.5; g.lineCap = 'round'; g.shadowColor = col; g.shadowBlur = p.scale * 1.2
  if (dashed) g.setLineDash([p.scale * 1.3, p.scale * 1.1])
  g.beginPath(); pts.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.stroke(); g.restore()
}

function ring(p: Surface, x: number, y: number, u: number, t: number) {
  const g = p.ctx
  g.save(); g.strokeStyle = p.theme.accent; g.lineWidth = p.scale * 0.35; g.shadowColor = p.theme.accent; g.shadowBlur = p.scale
  g.beginPath(); g.arc(x, y, p.scale * (1.9 + 0.25 * Math.sin(t * 6)), -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * u); g.stroke(); g.restore()
}

function tag(p: Surface, x: number, y: number, text: string, u: number) {
  const g = p.ctx, s = p.scale
  g.save(); g.globalAlpha = u; g.font = `700 ${s * 2.1}px ${DISPLAY}`
  const w = g.measureText(text.toUpperCase()).width + s * 1.8, h = s * 3.1
  const flip = y - s * 7 < p.sy(0)
  const by = flip ? y + s * 3.2 : y - s * 6.6
  g.fillStyle = p.theme.accent; g.fillRect(x - w / 2, by, w, h)
  g.fillRect(x - s * 0.15, flip ? y + s * 2 : by + h, s * 0.3, s * 1.2)
  g.fillStyle = p.theme.accentInk; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text.toUpperCase(), x, by + h / 2 + s * 0.1)
  g.restore()
}

function lowerThird(p: Surface, q: Cue, t: number, occupied: [number, number][]) {
  const g = p.ctx, s = p.scale
  const k = ease((t - q.t) / 0.5), out = Math.max(0, (t - (q.t + q.d - 0.35)) / 0.35)
  g.font = `800 ${s * 4.6}px ${DISPLAY}`
  const w = Math.max(g.measureText(q.text ?? '').width, (g.font = `500 ${s * 2.4}px ${BODY}`, g.measureText(q.sub ?? '').width)) + s * 7
  const h = s * 10
  // top left, under the score bug: the bottom of the frame belongs to the captions
  let box = { x: p.sx(2), y: p.sy(7), w, h }
  if (hits(box, occupied)) box = { ...box, y: p.sy(WID * 0.5) - h / 2 }
  g.save(); g.globalAlpha = 1 - out; g.translate(-s * 6 * (1 - k), 0)
  g.fillStyle = p.theme.accent; g.fillRect(box.x, box.y, s * 1.1, h)
  g.fillStyle = p.theme.panel; g.fillRect(box.x + s * 1.1, box.y, (w - s * 1.1) * k, h)
  g.fillStyle = p.theme.gfxInk; g.font = `800 ${s * 4.6}px ${DISPLAY}`; g.textAlign = 'left'; g.textBaseline = 'top'
  g.fillText(q.text ?? '', box.x + s * 3.2, box.y + s * 1.1)
  g.font = `500 ${s * 2.4}px ${BODY}`; g.fillStyle = p.theme.gfxMuted; g.fillText(q.sub ?? '', box.x + s * 3.3, box.y + s * 6.3)
  g.restore()
}

function statCard(p: Surface, q: Cue, t: number, occupied: [number, number][]) {
  const g = p.ctx, s = p.scale, rows = q.rows ?? []
  const k = ease((t - q.t) / 0.5)
  // size the card to its text: a row whose label and value don't fit side by side stacks them
  const pad = s * 2.4, gap = s * 2.5, maxW = s * 52
  const title = (q.text ?? '').toUpperCase()
  const fonts = { title: `800 ${s * 3.4}px ${DISPLAY}`, label: `500 ${s * 2.3}px ${BODY}`, val: `700 ${s * 3.3}px ${DISPLAY}` }
  const measure = (font: string, text: string) => (g.font = font, g.measureText(text).width)
  const titleW = measure(fonts.title, title)
  const sizes = rows.map(([label, val]) => ({ lw: measure(fonts.label, label), vw: measure(fonts.val, val) }))
  const side = Math.max(titleW, ...sizes.map(z => z.lw + gap + z.vw)) + pad * 2
  const w = Math.max(s * 30, Math.min(maxW, side))
  const stacked = sizes.map(z => z.lw + gap + z.vw > w - pad * 2)
  const rowH = stacked.map(st => s * (st ? 8.2 : 5.2))
  const h = s * 7.5 + rowH.reduce((a, b) => a + b, 0)
  let box = { x: p.sx(LEN - 2) - w, y: p.sy(8), w, h }
  if (hits(box, occupied)) box = { ...box, y: p.sy(WID - 2) - h - s * 8 }
  g.save(); g.globalAlpha = k; g.translate(s * 4 * (1 - k), 0)
  g.fillStyle = p.theme.panel; g.fillRect(box.x, box.y, w, h)
  g.fillStyle = p.theme.accent; g.fillRect(box.x, box.y, w, s * 0.7)
  g.fillStyle = p.theme.gfxInk; g.font = fonts.title; g.textAlign = 'left'; g.textBaseline = 'top'
  const tScale = Math.min(1, (w - pad * 2) / Math.max(titleW, 1))  // a long name shrinks rather than spill
  g.save(); g.translate(box.x + pad, box.y + s * 2.2); g.scale(tScale, tScale); g.fillText(title, 0, 0); g.restore()
  let y = box.y + s * 7.6
  rows.forEach(([label, val], i) => {
    const r = ease((t - q.t - 0.2 * (i + 1)) / 0.5)
    g.globalAlpha = k * r
    g.font = fonts.label; g.fillStyle = p.theme.gfxMuted; g.textAlign = 'left'
    g.fillText(fit(g, label, w - pad * 2), box.x + pad, y + s * 0.6)
    g.font = fonts.val; g.fillStyle = p.theme.accent; g.textAlign = 'right'
    g.fillText(val, box.x + w - pad, stacked[i] ? y + s * 3.6 : y)
    y += rowH[i]
  })
  g.restore()
}

/** Trim text with an ellipsis to fit a width, in the context's current font. */
function fit(g: CanvasRenderingContext2D, text: string, width: number): string {
  if (g.measureText(text).width <= width) return text
  let t = text
  while (t.length > 1 && g.measureText(t + '…').width > width) t = t.slice(0, -1)
  return t + '…'
}

export function bug(p: Surface, text: string, frozen: boolean) {
  const g = p.ctx, s = p.scale
  g.save(); g.font = `700 ${s * 2.6}px ${DISPLAY}`
  const w = g.measureText(text).width + s * 3, h = s * 4
  g.fillStyle = p.theme.panel; g.fillRect(p.sx(1.5), p.sy(1.5), w, h)
  g.fillStyle = p.theme.gfxInk; g.textAlign = 'left'; g.textBaseline = 'middle'; g.fillText(text, p.sx(1.5) + s * 1.5, p.sy(1.5) + h / 2 + s * 0.1)
  if (frozen) {
    g.font = `800 ${s * 2.4}px ${DISPLAY}`
    const tw = g.measureText('❚❚ ANALYSIS').width + s * 3
    g.fillStyle = p.theme.panel; g.fillRect(p.sx(LEN - 1.5) - tw, p.sy(1.5), tw, h)
    g.fillStyle = p.theme.accent; g.textAlign = 'right'
    g.fillText('❚❚ ANALYSIS', p.sx(LEN - 1.5) - s * 1.5, p.sy(1.5) + h / 2 + s * 0.1)
  }
  g.restore()
}

/** The same segment, in the 3D stadium: ground graphics on the grass, tags and broadcast graphics on top. */
export function renderSegment3D(st: Stadium, seg: Segment, t: number, numbers: number[], clock: string) {
  const th = st.theme
  const { clip, frozen } = clipTime(seg, t)
  const f = sampleClip(seg, clip)
  const active = seg.cues.filter(q => t >= q.t && t < q.t + q.d)
  const focus = new Set(active.filter(q => q.kind === 'highlight' || q.kind === 'run').map(q => q.slot!))
  const freezeCue = active.find(q => q.kind === 'freeze')
  const dim = frozen && freezeCue ? 0.45 * ease((t - freezeCue.t) / 0.35) : 0
  st.begin(dim)
  const G = st.ground
  for (const q of active) {
    const u = ease((t - q.t) / Math.min(q.d, 0.9))
    if (q.kind === 'zone' && q.rect) drawZone(G, q, u, t)
    if (q.kind === 'line' && q.x !== undefined) drawLine(G, q, u)
  }
  st.setFrame(f, focus.size ? focus : null, numbers)
  for (const q of active) {
    const u = ease((t - q.t) / Math.min(q.d * 0.6, 0.9))
    if (q.kind === 'arrow' && q.from && q.to) arrow(G, G.sx(q.from[0]), G.sy(q.from[1]), G.sx(q.to[0]), G.sy(q.to[1]), u,
      q.style === 'pass' || q.style === 'carry' ? '#ffffff' : th.accent, q.style === 'carry', q.style === 'cross' ? 0.5 : q.style === 'shot' ? 0.05 : 0.15)
    if (q.kind === 'run' && q.slot !== undefined) {
      const pts: [number, number][] = []
      const n = 24, a = q.from_t ?? 0, b = q.to_t ?? a + 3
      for (let k = 0; k <= n * u; k++) {
        const fr = sampleClip(seg, a + (b - a) * k / n)
        pts.push([G.sx(fr[3 + 2 * q.slot]), G.sy(fr[4 + 2 * q.slot])])
      }
      polyline(G, pts, th.accent, true)
    }
  }
  st.render(f)  // place the camera before projecting tags
  const occupied: [number, number][] = [st.project(f[0], f[1])]
  for (const q of active) {
    if (q.kind === 'highlight' && q.slot !== undefined) {
      const px = f[3 + 2 * q.slot], py = f[4 + 2 * q.slot]
      ring(G, G.sx(px), G.sy(py), ease((t - q.t) / 0.5), t)
      const [hx, hy] = st.project(px, py, 3.4)
      occupied.push([hx, hy])
      if (q.text) tag(st.hud, hx, hy + st.hud.scale * 6.6, q.text, ease((t - q.t) / 0.4))
    }
  }
  for (const q of active) {
    if (q.kind === 'label') lowerThird(st.hud, q, t, occupied)
    if (q.kind === 'statcard') statCard(st.hud, q, t, occupied)
  }
  bug(st.hud, clock, frozen)
  st.render(f)  // again, with this frame's ground graphics
}
