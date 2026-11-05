// vpn-rotate - rotate ProtonVPN servers when an AI provider rate-limits our IP.
//
// Loaded by opencode from ~/.config/opencode/plugins/ (this file) using the
// legacy plugin API: a named export that returns a hooks object.
//
// Strategy (fetch-injection): the `config` hook wraps each target provider's
// `options.fetch`. When a response looks like a per-IP limit, we ask the
// `pvpn-rotate` CLI to cool the current server and move to the fastest free NL
// server, then replay the request on the new IP.
//
// Modes (stored by `pvpn-rotate`, default "ask"):
//   off  - observe and log only
//   ask  - log, print an actionable hint, but do not switch
//   auto - switch the VPN and retry automatically
//
// Tuning via env:
//   PVPN_ROTATE_BIN        path to the CLI (default: `pvpn-rotate` on PATH)
//   PVPN_ROTATE_PROVIDERS  comma-separated provider ids to wrap
//   PVPN_ROTATE_MAX_TRIES  attempts per request (default 3)

import { execFile } from "node:child_process"
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

const STATE_DIR = path.join(os.homedir(), ".local", "state", "pvpn-rotate")
const STATE_FILE = path.join(STATE_DIR, "state.json")
const PLUGIN_LOG = path.join(STATE_DIR, "plugin.log")
const DEFAULT_PROVIDERS = ["opencode"]
const MAX_TRIES = Number(process.env.PVPN_ROTATE_MAX_TRIES || 3)
const SWITCH_TIMEOUT_MS = 120_000

const LIMIT_BODY_RE =
  /limit reached|rate.?limit|too many requests|quota|resource.?exhausted|free usage|usage limit|try again later/i

function resolveBin() {
  if (process.env.PVPN_ROTATE_BIN) return process.env.PVPN_ROTATE_BIN
  const fallback = path.join(os.homedir(), ".local", "bin", "pvpn-rotate")
  return existsSync(fallback) ? fallback : "pvpn-rotate"
}

const BIN = resolveBin()

function log(record) {
  try {
    mkdirSync(STATE_DIR, { recursive: true })
    appendFileSync(PLUGIN_LOG, JSON.stringify({ ts: Date.now(), ...record }) + "\n")
  } catch {
    // never let logging break a request
  }
}

function readMode() {
  try {
    const data = JSON.parse(readFileSync(STATE_FILE, "utf8"))
    const mode = data && data.mode
    return mode === "off" || mode === "auto" ? mode : "ask"
  } catch {
    return "ask"
  }
}

function runRotate(args, timeoutMs = SWITCH_TIMEOUT_MS) {
  return new Promise((resolve) => {
    execFile(BIN, args, { timeout: timeoutMs, maxBuffer: 1 << 20 }, (err, stdout) => {
      if (err && !stdout) return resolve({ ok: false, error: String((err && err.message) || err) })
      try {
        resolve(JSON.parse(stdout))
      } catch {
        resolve({ ok: false, error: "unparseable pvpn-rotate output", stdout: String(stdout).slice(0, 400) })
      }
    })
  })
}

async function inspect(res) {
  const status = res.status
  const retryAfter =
    (res.headers && (res.headers.get("retry-after") || res.headers.get("retry-after-ms"))) || null
  let body = ""
  let bodyLimit = false
  if (status >= 400) {
    try {
      body = await res.clone().text()
    } catch {
      body = ""
    }
    bodyLimit = LIMIT_BODY_RE.test(body)
  }
  const hit = status === 429 || bodyLimit || (retryAfter && status >= 400)
  const reason = `status=${status}${retryAfter ? ` retry-after=${retryAfter}` : ""}${bodyLimit ? " body-limit" : ""}`
  return { hit: Boolean(hit), reason, status, body: body.slice(0, 300), retryAfter }
}

function makeWrappedFetch(providerID, prevFetch) {
  const base = prevFetch || fetch
  return async function wrappedFetch(input, init) {
    let current = input
    for (let attempt = 1; attempt <= MAX_TRIES; attempt++) {
      const replayable =
        current instanceof Request ? current.clone() : !(init && init.body && typeof init.body.getReader === "function")
      const replaySource = current instanceof Request ? current.clone() : current

      const res = await base(current, current instanceof Request ? undefined : init)
      const info = await inspect(res)
      if (!info.hit) return res

      const mode = readMode()
      log({
        event: "detect",
        provider: providerID,
        attempt,
        mode,
        reason: info.reason,
        body: info.body,
      })

      if (mode === "off" || mode === "ask") {
        if (mode === "ask") {
          console.error(
            `[vpn-rotate] IP rate-limited (${info.reason}). Run: pvpn-rotate switch --reason "opencode limit"`,
          )
        }
        return res
      }

      if (attempt >= MAX_TRIES || !replayable) {
        log({ event: "give-up", provider: providerID, attempt, replayable })
        return res
      }

      const switched = await runRotate(["switch", "--json", "--reason", info.reason])
      log({ event: "switch", provider: providerID, result: switched })
      if (!switched || !switched.ok) return res

      current = replaySource
    }
    return base(input, input instanceof Request ? undefined : init)
  }
}

export const VpnRotate = async () => {
  log({ event: "init", bin: BIN })
  return {
    config: async (cfg) => {
      cfg.provider = cfg.provider || {}
      const envList = (process.env.PVPN_ROTATE_PROVIDERS || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
      const ids = new Set([...DEFAULT_PROVIDERS, ...envList, ...Object.keys(cfg.provider)])
      for (const id of ids) {
        const provider = (cfg.provider[id] = cfg.provider[id] || {})
        provider.options = provider.options || {}
        const prev = provider.options.fetch
        provider.options.fetch = makeWrappedFetch(id, prev)
      }
      log({ event: "config", wrapped: [...ids] })
    },
  }
}
