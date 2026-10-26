/** The owner token of each match this browser started: only that browser may skip, restart the
 * second half or switch agents off, because those change the match for everyone watching it. */
const KEY = 'rundown.owners'

function load(): Record<string, string> {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') } catch { return {} }
}

export function rememberOwner(runId: string, token?: string) {
  if (!token) return
  const all = { ...load(), [runId]: token }
  try { sessionStorage.setItem(KEY, JSON.stringify(all)) } catch { /* storage blocked: kept for this page only */ }
  memory[runId] = token
}

const memory: Record<string, string> = {}

/** Headers for a request about a run: its owner token when this browser started it. */
export function ownerHeaders(url: string): Record<string, string> {
  const m = url.match(/\/api\/runs\/([^/?]+)\//)
  const token = m ? (memory[m[1]] ?? load()[m[1]]) : undefined
  return token ? { 'x-run-owner': token } : {}
}

/** POST with JSON and the owner token when there is one. */
export function postRun(url: string, body?: unknown) {
  return fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', ...ownerHeaders(url) },
    body: body === undefined ? undefined : JSON.stringify(body) })
}
