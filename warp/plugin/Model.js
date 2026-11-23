.pragma library

// Pure helpers for the WARP bar widget. No QML types, so tests run them under
// node:vm (tests/warp-model.mjs).

// Exit code the runner shell uses when the warp-cli binary is not on PATH.
var EXIT_NOT_INSTALLED = 127

// warp-cli `-j status` prints {"status": "...", "reason": ...}. The status is
// one of the daemon states below; the reason is a string ("NetworkHealthy") or
// a one-key object for struct/newtype variants ({"RegistrationMissing": ...}).
var STATES = {
  connected: "connected",
  connecting: "connecting",
  disconnecting: "disconnecting",
  disconnected: "disconnected",
  paused: "paused",
  unable: "unable",
  unregistered: "unregistered",
  daemonDown: "daemon-down",
  notInstalled: "not-installed",
  unknown: "unknown"
}

// "NetworkHealthy" -> "Network healthy"; leaves already-spaced text alone.
function humanize(word) {
  var text = String(word === undefined || word === null ? "" : word).trim()
  if (text === "") return ""
  if (/\s/.test(text)) return text
  var spaced = text.replace(/_/g, " ").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
  var lower = spaced.toLowerCase()
  // Keep common acronyms upper-case.
  lower = lower.replace(/\b(dns|doh|mtls|tls|api|lan|ip|socks|wifi)\b/g, function(m) {
    return m === "wifi" ? "Wi-Fi" : m === "mtls" ? "mTLS" : m.toUpperCase()
  })
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

// Reason value (string, object, array, null) -> one readable line.
function reasonText(reason) {
  if (reason === undefined || reason === null) return ""
  if (typeof reason === "string" || typeof reason === "number" || typeof reason === "boolean") return humanize(String(reason))
  if (Array.isArray(reason)) {
    var parts = []
    for (var i = 0; i < reason.length; i++) {
      var p = reasonText(reason[i])
      if (p !== "") parts.push(p)
    }
    return parts.join(", ")
  }
  if (typeof reason === "object") {
    var keys = Object.keys(reason)
    if (keys.length === 0) return ""
    var head = humanize(keys[0])
    var tail = reasonText(reason[keys[0]])
    return tail === "" ? head : head + ": " + tail
  }
  return ""
}

// Raw reason flattened for matching, keeping the original identifiers.
function reasonKeys(reason) {
  try {
    return JSON.stringify(reason === undefined ? null : reason)
  } catch (e) {
    return ""
  }
}

function stateFromStatus(status, reason) {
  var s = String(status || "").replace(/\s+/g, "").toLowerCase()
  var keys = reasonKeys(reason) + " " + String(status || "")
  // Daemon reasons say RegistrationMissing; IPC errors say missing_registration.
  if (/Registration\s*Missing|Missing_?Registration/i.test(keys)) return STATES.unregistered
  if (s === "connected") return STATES.connected
  if (s === "connecting") return STATES.connecting
  if (s === "disconnecting") return STATES.disconnecting
  if (s === "disconnected") return STATES.disconnected
  if (s === "paused") return STATES.paused
  if (s === "unable") return STATES.unable
  return STATES.unknown
}

var LABELS = {
  "connected": "Connected",
  "connecting": "Connecting",
  "disconnecting": "Disconnecting",
  "disconnected": "Disconnected",
  "paused": "Paused",
  "unable": "Unable to connect",
  "unregistered": "Not enrolled",
  "daemon-down": "Service not running",
  "not-installed": "WARP not installed",
  "unknown": "Unknown"
}

function label(state) {
  return LABELS[state] || LABELS.unknown
}

// States that deserve the attention badge in the bar.
function needsAttention(state) {
  return state === STATES.unable || state === STATES.unregistered || state === STATES.daemonDown
    || state === STATES.notInstalled || state === STATES.unknown
}

// States in which the toggle means "disconnect".
function isOn(state) {
  return state === STATES.connected || state === STATES.connecting
}

function firstLine(text) {
  var lines = String(text || "").split("\n")
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim()
    if (line !== "") return line.length > 160 ? line.substring(0, 157) + "…" : line
  }
  return ""
}

// One `warp-cli -j status` run -> view state.
//   { state, label, status, reason, detail, attention, on }
// `detail` is the readable reason (or error) for tooltips and the panel.
function parseStatus(stdout, stderr, exitCode) {
  var out = String(stdout || "")
  var err = String(stderr || "")
  var result = function(state, status, reason, detail) {
    return {
      state: state,
      label: label(state),
      status: status,
      reason: reason,
      detail: detail,
      attention: needsAttention(state),
      on: isOn(state)
    }
  }

  if (exitCode === EXIT_NOT_INSTALLED) return result(STATES.notInstalled, "", null, "warp-cli was not found on PATH")

  var parsed = null
  var trimmed = out.trim()
  if (trimmed.charAt(0) === "{") {
    try {
      parsed = JSON.parse(trimmed)
    } catch (e) {
      parsed = null
    }
  }

  if (parsed && typeof parsed === "object" && parsed.status !== undefined) {
    var status = String(parsed.status)
    var state = stateFromStatus(status, parsed.reason)
    return result(state, status, parsed.reason === undefined ? null : parsed.reason, reasonText(parsed.reason))
  }

  // Errors under -j: {"code": "FailedToConnectToDaemon", "error": "No such file or directory (os error 2)"}
  if (parsed && typeof parsed === "object" && typeof parsed.code === "string") {
    var why = parsed.error ? " (" + String(parsed.error) + ")" : ""
    if (parsed.code === "FailedToConnectToDaemon") return result(STATES.daemonDown, "", null, "warp-svc is not running" + why)
    if (stateFromStatus("", parsed.code) === STATES.unregistered) return result(STATES.unregistered, "", null, humanize(parsed.code) + why)
    return result(STATES.unknown, "", null, humanize(parsed.code) + why)
  }

  var combined = err + "\n" + out
  if (/daemon/i.test(combined) && /(connect|running|reach)/i.test(combined)) {
    return result(STATES.daemonDown, "", null, firstLine(err) || firstLine(out))
  }
  if (exitCode !== 0) return result(STATES.unknown, "", null, firstLine(err) || firstLine(out) || ("warp-cli exited with " + exitCode))
  return result(STATES.unknown, "", null, firstLine(out) || "Unrecognised warp-cli output")
}

// `warp-cli -j registration show` -> { organization, accountType, managed } or null.
// Only names that are safe to show on the user's own screen are kept; ids,
// keys and fingerprints are dropped here so they never reach QML properties.
function parseRegistration(stdout) {
  var parsed = null
  try {
    parsed = JSON.parse(String(stdout || "").trim())
  } catch (e) {
    return null
  }
  if (!parsed || typeof parsed !== "object") return null
  var account = parsed.account && typeof parsed.account === "object" ? parsed.account : {}
  return {
    organization: typeof account.organization === "string" ? account.organization : "",
    accountType: typeof account.type === "string" ? account.type : "",
    managed: parsed.managed === true
  }
}

// Bar tooltip: "WARP: Connected" plus the reason when it adds something.
function tooltip(view, organization) {
  if (!view) return "Cloudflare WARP"
  var text = "Cloudflare WARP: " + view.label
  if (view.detail && view.detail !== view.label && view.detail !== "Network healthy") text += "\n" + view.detail
  if (organization) text += "\nOrganization: " + organization
  return text
}

// First https URL in a blob of CLI output (registration prints one when it
// cannot open a browser itself).
function firstUrl(text) {
  var match = String(text || "").match(/https:\/\/[^\s"'<>]+/)
  return match ? match[0] : ""
}
