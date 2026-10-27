// The 3D stadium: the match drawn the way a broadcast camera sees it.
//
// Three layers, so the telestration code stays the same as on the flat pitch:
//   - the WebGL scene: grass, lines, goals, boards, stands, lit players with shadows, the ball;
//   - a ground overlay: a transparent canvas texture lying on the grass, drawn in pitch metres by
//     the same 2D code as before (arrows, runs, zones, lines, rings), put in perspective by the camera;
//   - a HUD canvas on top, in screen space: score bug, lower third, stat card, player name tags.
import * as THREE from 'three'
import type { Theme } from './renderer'

export const LEN = 105, WID = 68
const PX_PER_M = 20  // ground overlay resolution
const FIG = 1.3      // figures a little larger than life, so a broadcast-distance camera can read them

/** What the 2D drawing code needs: a context, a mapping from pitch metres to its pixels, a theme. */
export interface Surface {
  ctx: CanvasRenderingContext2D
  scale: number
  theme: Theme
  sx(x: number): number
  sy(y: number): number
}

export type CameraMode = 'live' | 'studio' | 'hero'

interface Fig { g: THREE.Group; body: THREE.Mesh; legL: THREE.Object3D; legR: THREE.Object3D; badge: THREE.Sprite; phase: number; prev: [number, number] | null; heading: number; mats: THREE.Material[] }

function pitchTexture(theme: { pitch: string; stripe: string; line: string }): THREE.CanvasTexture {
  const m = 16, pad = 6 // px per metre, apron in metres
  const c = document.createElement('canvas')
  c.width = (LEN + 2 * pad) * m; c.height = (WID + 2 * pad) * m
  const g = c.getContext('2d')!
  const X = (x: number) => (x + pad) * m, Y = (y: number) => (y + pad) * m
  g.fillStyle = theme.pitch; g.fillRect(0, 0, c.width, c.height)
  for (let i = 0; i < 18; i++) {
    if (i % 2) continue
    g.fillStyle = theme.stripe; g.fillRect(X(-pad + i * (LEN + 2 * pad) / 18), 0, (LEN + 2 * pad) / 18 * m, c.height)
  }
  // fine grain, so the grass doesn't read as flat paint
  const img = g.getImageData(0, 0, c.width, c.height), d = img.data
  for (let i = 0; i < d.length; i += 4) { const n = (Math.random() - 0.5) * 14; d[i] += n; d[i + 1] += n; d[i + 2] += n * 0.6 }
  g.putImageData(img, 0, 0)
  g.strokeStyle = 'rgba(255,255,255,0.92)'; g.lineWidth = 0.14 * m
  g.strokeRect(X(0), Y(0), LEN * m, WID * m)
  g.beginPath(); g.moveTo(X(52.5), Y(0)); g.lineTo(X(52.5), Y(WID)); g.stroke()
  g.beginPath(); g.arc(X(52.5), Y(34), 9.15 * m, 0, Math.PI * 2); g.stroke()
  g.fillStyle = 'rgba(255,255,255,0.92)'
  g.beginPath(); g.arc(X(52.5), Y(34), 0.25 * m, 0, Math.PI * 2); g.fill()
  for (const [x0, dir] of [[0, 1], [LEN, -1]] as const) {
    const box = (dd: number, w: number) => g.strokeRect(Math.min(X(x0), X(x0 + dir * dd)), Y(34 - w / 2), dd * m, w * m)
    box(16.5, 40.32); box(5.5, 18.32)
    g.beginPath(); g.arc(X(x0 + dir * 11), Y(34), 0.25 * m, 0, Math.PI * 2); g.fill()
    g.beginPath(); g.arc(X(x0 + dir * 11), Y(34), 9.15 * m, dir > 0 ? -0.927 : Math.PI - 0.927, dir > 0 ? 0.927 : Math.PI + 0.927); g.stroke()
  }
  for (const [x, y] of [[0, 0], [LEN, 0], [0, WID], [LEN, WID]]) {
    g.beginPath(); g.arc(X(x), Y(y), 1 * m, 0, Math.PI * 2); g.stroke()
  }
  const t = new THREE.CanvasTexture(c)
  t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8
  return t
}

function boardsTexture(accent: string): THREE.CanvasTexture {
  const c = document.createElement('canvas'); c.width = 2048; c.height = 64
  const g = c.getContext('2d')!
  g.fillStyle = '#05080a'; g.fillRect(0, 0, c.width, c.height)
  g.font = "800 44px 'Barlow Condensed', sans-serif"; g.textBaseline = 'middle'
  g.fillStyle = accent; g.fillText('RUNDOWN FOR YOU', 60, 34)
  g.fillStyle = '#f2f0e8'; g.fillText('EVERY FAN  ·  EVERY LANGUAGE', 1080, 34)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping
  return t
}

function crowdTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas'); c.width = 1024; c.height = 256
  const g = c.getContext('2d')!
  g.fillStyle = '#11161b'; g.fillRect(0, 0, c.width, c.height)
  const tones = ['#1d2329', '#232a31', '#2a2f36', '#2f2a30', '#262f3a', '#30262a', '#33363a']
  for (let y = 0; y < c.height; y += 5) {
    for (let x = (y / 5) % 2 ? 0 : 2; x < c.width; x += 5) {
      g.fillStyle = tones[(Math.random() * tones.length) | 0]; g.fillRect(x, y, 4, 4)
    }
    if (y % 40 === 0) { g.fillStyle = 'rgba(0,0,0,0.35)'; g.fillRect(0, y, c.width, 2) }
  }
  const fade = g.createLinearGradient(0, 0, 0, c.height)
  fade.addColorStop(0, 'rgba(4,6,8,0.75)'); fade.addColorStop(0.6, 'rgba(4,6,8,0.15)'); fade.addColorStop(1, 'rgba(4,6,8,0)')
  g.fillStyle = fade; g.fillRect(0, 0, c.width, c.height)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping
  return t
}

function badgeTexture(num: string, bg: string, ink: string): THREE.CanvasTexture {
  const c = document.createElement('canvas'); c.width = c.height = 128
  const g = c.getContext('2d')!
  g.fillStyle = bg; g.beginPath(); g.arc(64, 64, 54, 0, Math.PI * 2); g.fill()
  g.lineWidth = 8; g.strokeStyle = 'rgba(0,0,0,0.55)'; g.stroke()
  g.fillStyle = ink; g.font = "800 70px 'Barlow Condensed', sans-serif"; g.textAlign = 'center'; g.textBaseline = 'middle'
  g.fillText(num, 64, 68)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  return t
}

const SKIN = ['#8d5524', '#c68642', '#e0ac69', '#f1c27d', '#ffdbac', '#6b4423', '#a0673c']

export class Stadium {
  readonly gl: THREE.WebGLRenderer
  readonly hudCanvas: HTMLCanvasElement
  readonly hud: Surface
  readonly ground: Surface
  theme!: Theme
  colours: [string, string] = ['#f4f1ea', '#e5484d']
  inks: [string, string] = ['#0b1a12', '#ffffff']
  mode: CameraMode = 'live'
  /** For a page with copy on the left: shift the picture right by this share of the width, and keep the HUD to the right of hudX0. */
  shiftX = 0
  hudX0 = 0
  private scene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera(26, 16 / 9, 1, 900)
  private figs: Fig[] = []
  private ball!: THREE.Mesh
  private ballShadow!: THREE.Mesh
  private overlayTex: THREE.CanvasTexture
  private overlayCanvas: HTMLCanvasElement
  private grass!: THREE.Mesh
  private dimMat!: THREE.MeshBasicMaterial
  private look = new THREE.Vector3(52.5, 0, 34)
  private camPos = new THREE.Vector3(52.5, 30, 112)
  private numbers: string = ''
  private builtFor = ''
  private last = performance.now()

  constructor(readonly root: HTMLElement) {
    this.gl = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance', preserveDrawingBuffer: false })
    this.gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.gl.shadowMap.enabled = true
    this.gl.shadowMap.type = THREE.PCFSoftShadowMap
    this.gl.toneMapping = THREE.ACESFilmicToneMapping
    this.gl.toneMappingExposure = 1.05
    this.gl.outputColorSpace = THREE.SRGBColorSpace
    Object.assign(this.gl.domElement.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' })
    this.hudCanvas = document.createElement('canvas')
    Object.assign(this.hudCanvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none' })
    if (getComputedStyle(root).position === 'static') root.style.position = 'relative'
    root.append(this.gl.domElement, this.hudCanvas)

    this.overlayCanvas = document.createElement('canvas')
    this.overlayCanvas.width = LEN * PX_PER_M; this.overlayCanvas.height = WID * PX_PER_M
    this.overlayTex = new THREE.CanvasTexture(this.overlayCanvas)
    this.overlayTex.colorSpace = THREE.SRGBColorSpace; this.overlayTex.anisotropy = 8
    const self = this
    this.ground = {
      ctx: this.overlayCanvas.getContext('2d')!, scale: PX_PER_M, get theme() { return self.theme },
      sx: (x: number) => x * PX_PER_M, sy: (y: number) => y * PX_PER_M,
    }
    const hctx = this.hudCanvas.getContext('2d')!
    this.hud = {
      ctx: hctx, get theme() { return self.theme },
      get scale() { return Math.min(self.hudCanvas.height / 72, self.hudCanvas.width * (1 - self.hudX0) / (LEN + 4)) },
      sx(x: number) { const w = self.hudCanvas.width, x0 = w * self.hudX0; return x0 + (w - x0 - LEN * this.scale) / 2 + x * this.scale },
      sy(y: number) { return (self.hudCanvas.height - WID * this.scale) / 2 + y * this.scale },
    }
    this.buildStatic()
    this.resize()
  }

  private buildStatic() {
    const s = this.scene
    s.background = new THREE.Color('#06090b')
    s.fog = new THREE.Fog('#06090b', 160, 420)
    s.add(new THREE.HemisphereLight('#e8f0ff', '#24401c', 1.15))
    const sun = new THREE.DirectionalLight('#fff6e6', 2.6)
    sun.position.set(20, 90, -40); sun.target.position.set(52.5, 0, 34)
    sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    Object.assign(sun.shadow.camera, { left: -70, right: 70, top: 55, bottom: -55, near: 10, far: 220 })
    sun.shadow.bias = -0.0005; sun.shadow.radius = 3
    s.add(sun, sun.target)
    // floodlight glow from the four corners
    for (const [x, z] of [[-18, -22], [123, -22], [-18, 90], [123, 90]]) {
      const l = new THREE.PointLight('#e8f0ff', 900, 220, 1.6); l.position.set(x, 45, z); s.add(l)
    }

    // the stands: dark tiers with a speckled crowd, open on the camera's side
    const crowd = crowdTexture()
    const tierMat = (rx: number) => { const t = crowd.clone(); t.repeat.set(rx, 1); t.needsUpdate = true; return new THREE.MeshStandardMaterial({ map: t, roughness: 1 }) }
    const stand = (w: number, x: number, z: number, ry: number) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, 26), tierMat(w / 30))
      m.position.set(x, 10, z); m.rotation.set(-0.55, ry, 0, 'YXZ'); s.add(m)
    }
    stand(170, 52.5, -24, 0)
    stand(110, -26, 34, Math.PI / 2)
    stand(110, 131, 34, -Math.PI / 2)

    // advertising boards around the pitch
    const bt = boardsTexture('#d8ff3c')
    const board = (w: number, x: number, z: number, ry: number) => {
      const t = bt.clone(); t.repeat.set(w / 64, 1); t.needsUpdate = true
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, 1), new THREE.MeshBasicMaterial({ map: t }))
      m.position.set(x, 0.5, z); m.rotation.y = ry; s.add(m)
    }
    board(118, 52.5, -5.5, 0)
    board(80, -6, 34, Math.PI / 2)
    board(80, 111, 34, -Math.PI / 2)

    // goals: posts, bar and a net
    const post = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.4 })
    const net = new THREE.MeshBasicMaterial({ color: '#ffffff', wireframe: true, transparent: true, opacity: 0.22 })
    for (const [x, dir] of [[0, -1], [LEN, 1]] as const) {
      const g = new THREE.Group()
      for (const z of [34 - 3.66, 34 + 3.66]) {
        const p = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 2.44, 10), post); p.position.set(0, 1.22, z); p.castShadow = true; g.add(p)
      }
      const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 7.32, 10), post)
      bar.rotation.x = Math.PI / 2; bar.position.set(0, 2.44, 34); bar.castShadow = true; g.add(bar)
      const n = new THREE.Mesh(new THREE.BoxGeometry(2, 2.44, 7.32, 6, 8, 18), net); n.position.set(dir, 1.22, 34); g.add(n)
      g.position.x = x; s.add(g)
    }
    this.ball = new THREE.Mesh(new THREE.SphereGeometry(0.22 * FIG, 20, 14), new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.35 }))
    this.ball.castShadow = true; s.add(this.ball)
    this.ballShadow = new THREE.Mesh(new THREE.CircleGeometry(0.3 * FIG, 16), new THREE.MeshBasicMaterial({ color: '#000000', transparent: true, opacity: 0.35 }))
    this.ballShadow.rotation.x = -Math.PI / 2; s.add(this.ballShadow)

    // the overlay plane, a hair above the grass
    const ov = new THREE.Mesh(new THREE.PlaneGeometry(LEN, WID),
      new THREE.MeshBasicMaterial({ map: this.overlayTex, transparent: true, depthWrite: false, toneMapped: false }))
    ov.rotation.x = -Math.PI / 2; ov.position.set(LEN / 2, 0.03, WID / 2); ov.renderOrder = 2; s.add(ov)
    // a darkening veil for freezes and half-time
    this.dimMat = new THREE.MeshBasicMaterial({ color: '#000000', transparent: true, opacity: 0, depthWrite: false })
    const veil = new THREE.Mesh(new THREE.PlaneGeometry(LEN + 40, WID + 40), this.dimMat)
    veil.rotation.x = -Math.PI / 2; veil.position.set(LEN / 2, 0.02, WID / 2); veil.renderOrder = 1; s.add(veil)
  }

  /** The grass and the players depend on the theme and the clubs; rebuilt when they change. */
  private ensure(numbers: number[] | null) {
    const key = this.colours.join('|')
    if (key !== this.builtFor) {
      this.builtFor = key
      if (this.grass) { this.scene.remove(this.grass) }
      const tex = pitchTexture({ pitch: '#2f6b2e', stripe: '#377a35', line: '#ffffff' })
      this.grass = new THREE.Mesh(new THREE.PlaneGeometry(LEN + 12, WID + 12),
        new THREE.MeshStandardMaterial({ map: tex, roughness: 0.92, metalness: 0 }))
      this.grass.rotation.x = -Math.PI / 2; this.grass.position.set(LEN / 2, 0, WID / 2); this.grass.receiveShadow = true
      this.scene.add(this.grass)
      for (const f of this.figs) this.scene.remove(f.g)
      this.figs = []
      for (let s = 0; s < 22; s++) this.figs.push(this.makeFigure(s))
      this.numbers = ''
    }
    const nk = numbers ? numbers.join(',') : ''
    if (numbers && nk !== this.numbers) {
      this.numbers = nk
      this.figs.forEach((f, s) => {
        const side = s < 11 ? 0 : 1, gk = s % 11 === 0
        const old = f.badge.material.map; old?.dispose()
        f.badge.material.map = badgeTexture(String(numbers[s] ?? ''), gk ? '#f2c94c' : this.colours[side], gk ? '#111111' : this.inks[side])
        f.badge.material.needsUpdate = true
      })
    }
  }

  private makeFigure(s: number): Fig {
    const side = s < 11 ? 0 : 1, gk = s % 11 === 0
    const shirtC = gk ? '#f2c94c' : this.colours[side]
    const mat = (color: string, rough = 0.7) => new THREE.MeshStandardMaterial({ color, roughness: rough, transparent: true })
    const shirt = mat(shirtC), shorts = mat(side === 0 ? '#1d2a3a' : '#f2f0e8', 0.8)
    const socks = mat(side === 0 ? '#f2f0e8' : '#1d2a3a', 0.8), skin = mat(SKIN[(s * 7 + 3) % SKIN.length], 0.6)
    const hair = mat(['#1b1410', '#3b2a1a', '#0d0d0d', '#6b4a2b'][s % 4], 0.9), boots = mat('#0b0b0b', 0.5)
    const g = new THREE.Group()
    const add = (m: THREE.Mesh, parent: THREE.Object3D = g) => { m.castShadow = true; parent.add(m); return m }
    const body = add(new THREE.Mesh(new THREE.CapsuleGeometry(0.22, 0.42, 4, 12), shirt)); body.position.y = 1.3
    add(new THREE.Mesh(new THREE.CylinderGeometry(0.23, 0.21, 0.24, 12), shorts)).position.y = 0.92
    const head = add(new THREE.Mesh(new THREE.SphereGeometry(0.15, 14, 12), skin)); head.position.y = 1.78
    const cap = add(new THREE.Mesh(new THREE.SphereGeometry(0.155, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2.2), hair)); cap.position.y = 1.8
    const limb = (y: number, z: number, len: number, r: number, m: THREE.Material, foot?: THREE.Material) => {
      const pivot = new THREE.Object3D(); pivot.position.set(0, y, z); g.add(pivot)
      add(new THREE.Mesh(new THREE.CapsuleGeometry(r, len, 3, 8), m), pivot).position.y = -len / 2 - r
      if (foot) { const b = add(new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.08, 0.11), foot), pivot); b.position.set(0.05, -len - 2 * r + 0.02, 0) }
      return pivot
    }
    const legL = limb(0.84, 0.11, 0.56, 0.085, socks, boots), legR = limb(0.84, -0.11, 0.56, 0.085, socks, boots)
    const armL = limb(1.52, 0.27, 0.42, 0.06, skin), armR = limb(1.52, -0.27, 0.42, 0.06, skin)
    legL.userData.arm = armR; legR.userData.arm = armL
    const badge = new THREE.Sprite(new THREE.SpriteMaterial({ depthTest: false, transparent: true }))
    badge.scale.set(0.78, 0.78, 1); badge.position.y = 2.35; badge.renderOrder = 5; g.add(badge)
    g.scale.setScalar(FIG)
    this.scene.add(g)
    return { g, body, legL, legR, badge, phase: Math.random() * 6, prev: null, heading: side === 0 ? 0 : Math.PI,
      mats: [shirt, shorts, socks, skin, hair, boots] }
  }

  resize() {
    const r = this.root.getBoundingClientRect()
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height))
    this.gl.setSize(w, h, false)
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.hudCanvas.width = Math.round(w * dpr); this.hudCanvas.height = Math.round(h * dpr)
    this.camera.aspect = w / h
    if (this.shiftX) this.camera.setViewOffset(w, h, -w * this.shiftX, 0, w, h)
    else this.camera.clearViewOffset()
    this.camera.updateProjectionMatrix()
  }

  /** Pitch metres (and height) to HUD pixels. */
  project(x: number, y: number, z = 0): [number, number] {
    const v = new THREE.Vector3(x, z, y).project(this.camera)
    return [(v.x + 1) / 2 * this.hudCanvas.width, (1 - v.y) / 2 * this.hudCanvas.height]
  }

  /** Clear the ground overlay and the HUD before a frame's drawing. */
  begin(dim = 0) {
    this.ground.ctx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height)
    this.hud.ctx.clearRect(0, 0, this.hudCanvas.width, this.hudCanvas.height)
    this.dimMat.opacity = dim * 0.55
  }

  /** frame: [ball_x, ball_y, ball_z, 22 x (x, y)] */
  setFrame(f: number[], focus: Set<number> | null, numbers: number[] | null) {
    this.ensure(numbers)
    const now = performance.now(), dt = Math.min(0.1, (now - this.last) / 1000); this.last = now
    this.figs.forEach((fig, s) => {
      const x = f[3 + 2 * s], y = f[4 + 2 * s]
      let speed = 0
      if (fig.prev) {
        const dx = x - fig.prev[0], dy = y - fig.prev[1], d = Math.hypot(dx, dy)
        speed = dt > 0 ? d / Math.max(dt, 0.016) : 0
        if (d > 0.02) {
          const target = Math.atan2(-dy, dx)
          let diff = target - fig.heading; while (diff > Math.PI) diff -= 2 * Math.PI; while (diff < -Math.PI) diff += 2 * Math.PI
          fig.heading += diff * Math.min(1, dt * 8)
        }
      }
      fig.prev = [x, y]
      const run = Math.min(1, speed / 6)
      fig.phase += dt * (4 + speed * 1.6)
      const swing = Math.sin(fig.phase) * 0.75 * run
      fig.legL.rotation.z = swing; fig.legR.rotation.z = -swing
      ;(fig.legL.userData.arm as THREE.Object3D).rotation.z = swing * 0.8
      ;(fig.legR.userData.arm as THREE.Object3D).rotation.z = -swing * 0.8
      fig.body.rotation.z = -0.12 * run
      fig.g.position.set(x, Math.abs(Math.sin(fig.phase)) * 0.05 * run, y)
      fig.g.rotation.y = fig.heading
      const dimmed = focus && !focus.has(s)
      for (const m of fig.mats) (m as THREE.MeshStandardMaterial).opacity = dimmed ? 0.35 : 1
      fig.badge.material.opacity = dimmed ? 0.3 : 1
    })
    this.ball.position.set(f[0], 0.22 * FIG + f[2], f[1])
    this.ballShadow.position.set(f[0], 0.02, f[1])
    ;(this.ballShadow.material as THREE.MeshBasicMaterial).opacity = Math.max(0.08, 0.35 - f[2] * 0.03)
  }

  /** Where the ball is, so the camera can follow it. */
  private aim(f: number[] | null, snap = false) {
    const bx = f ? f[0] : 52.5, by = f ? f[1] : 34
    let pos: THREE.Vector3, look: THREE.Vector3
    if (this.mode === 'studio') {         // high and tight: the analysis view
      const cx = THREE.MathUtils.clamp(bx, 22, 83)
      look = new THREE.Vector3(cx, 0, THREE.MathUtils.clamp(by, 22, 46))
      pos = new THREE.Vector3(cx, 52, look.z + 66)
    } else if (this.mode === 'hero') {    // the landing page: wide, slow
      const cx = 52.5 + Math.sin(performance.now() / 9000) * 14
      look = new THREE.Vector3(cx, 0, 30); pos = new THREE.Vector3(cx - 20, 34, 104)
    } else {                              // the main broadcast camera on the halfway line
      const cx = THREE.MathUtils.clamp(bx, 26, 79)
      look = new THREE.Vector3(cx, 0, THREE.MathUtils.clamp(by * 0.55 + 15, 18, 42))
      pos = new THREE.Vector3(52.5 + (cx - 52.5) * 0.55, 31, 116)
    }
    const k = snap ? 1 : 0.06
    this.look.lerp(look, k); this.camPos.lerp(pos, k)
    this.camera.position.copy(this.camPos); this.camera.lookAt(this.look)
  }

  render(f: number[] | null, snap = false) {
    this.aim(f, snap)
    this.overlayTex.needsUpdate = true
    this.gl.render(this.scene, this.camera)
  }

  dispose() {
    this.gl.dispose()
    this.gl.domElement.remove(); this.hudCanvas.remove()
  }
}

export function webglAvailable(): boolean {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')) } catch { return false }
}
