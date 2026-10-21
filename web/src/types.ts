export interface PlayerMeta { id: string; name: string; number: number; role: string }
export interface ClubMeta { code: string; name: string; short: string; colour: string; players: PlayerMeta[]; starters: string[] }
export interface Meta { seed: number; home: ClubMeta; away: ClubMeta }

export interface Fact {
  key: string; stat: string; args: Record<string, unknown>; value: number | string; unit: string
  label: string; evidence: string[]; detail: Record<string, unknown>
}

export interface Candidate {
  kind: string; team: string; period: number; t: number; minute: number; strength: number
  headline: string; player: string | null; side: string | null; evidence: string[]; facts: Fact[]
}

export interface Cue {
  t: number; d: number
  kind: 'freeze' | 'label' | 'highlight' | 'run' | 'zone' | 'arrow' | 'line' | 'statcard'
  at?: number; slot?: number; text?: string; sub?: string
  from?: [number, number]; to?: [number, number]; from_t?: number; to_t?: number
  style?: 'pass' | 'cross' | 'shot' | 'carry'; rect?: [number, number, number, number]; x?: number
  rows?: [string, string][]; event_ids?: string[]; fact_keys?: string[]
}

export interface ScriptLine { t: number; d: number; text: string; fact_keys?: string[] }

export interface Segment {
  version: '1'; segment_id: string; title: string; kind: string; team: string | null; lang: string
  duration_s: number
  clip: { period: number; t0: number; t1: number; hz: number; slots: string[]; frames: number[][] }
  cues: Cue[]; script: ScriptLine[]; facts: Fact[]
  provenance: {
    made_by: string; candidate?: Candidate; verified?: boolean
    crew?: { telestrator: 'agent' | 'rules'; writer: 'agent' | 'rules' }; why?: string | null; pick_reason?: string
  }
}

export interface Rundown {
  slot_s: number; cut: string; lang: string; made_by: string; score: string
  intro: { d: number; home: string; away: string }
  segments: Segment[]; duration_s: number
}

export interface FeedItem {
  kind: string; text: string; clock: number; at: number; candidate?: Candidate; seconds_after_whistle?: number
  agent?: string; status?: string; why?: string
}

export interface TrailItem {
  seq: number; at: number; agent: string; action: string; status: string; ms: number | null; why: string; event_ids: string[]
}

export interface CrewInfo { mode: 'agents' | 'rules'; model: string | null; killed: string[] }

export interface RunSnapshot {
  id: string; seed: number; speed: number; status: string; clock: number; period: number
  score: [number, number]; meta: Meta; feed: FeedItem[]; candidates: Candidate[]
  locked_at: number | null; whistle_at: number | null; crew: CrewInfo
}

export interface MatchEvent { id: string; t: number; period: number; minute: number; type: string; team: string | null; player: string | null; [k: string]: unknown }
