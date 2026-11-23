import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// Talks to warp-svc through warp-cli. The daemon's socket is world-writable
// (srwxrwxrwx root), so no privilege is needed for status, connect or
// disconnect; organisation policy decides whether a disconnect is allowed.
//
// State comes from `warp-cli -j status`, polled. `warp-cli -l status` stays
// open and prints on every daemon change; any line it prints only schedules a
// re-poll, so the widget never depends on that stream's format.
Item {
  id: root

  property var settings: ({})

  property var view: Model.parseStatus("", "", -1)
  property string organization: ""
  property string accountType: ""
  property string actionStatus: ""
  property string lastError: ""
  property bool refreshing: false
  // -1 follow the daemon; 0/1 while a disconnect/connect is settling.
  property int _desired: -1
  property bool _registrationRead: false

  readonly property string warpCli: String(setting("warpCli", "warp-cli"))
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 30, 5, 3600)
  readonly property bool listenForChanges: setting("listenForChanges", true) !== false
  readonly property bool installed: view.state !== Model.STATES.notInstalled
  readonly property bool daemonUp: installed && view.state !== Model.STATES.daemonDown
  readonly property bool on: _desired === -1 ? view.on : _desired === 1
  readonly property bool canToggle: daemonUp && view.state !== Model.STATES.unregistered && view.state !== Model.STATES.unknown
  readonly property bool busy: controlProcess.running || _desired !== -1
  readonly property bool signingIn: flowProcess.running
  readonly property string tooltip: Model.tooltip(view, organization)

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var n = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(n)) n = fallback
    return Math.max(min, Math.min(max, n))
  }

  // Exit 127 when warp-cli is absent, so the model can say "not installed"
  // instead of reporting a spawn failure.
  function cli(args) {
    return ["sh", "-c", "command -v \"$1\" >/dev/null 2>&1 || exit 127; exec \"$@\"", "sh", root.warpCli, "--accept-tos"].concat(args)
  }

  function refresh() {
    if (statusProcess.running) {
      refreshAgain.restart()
      return
    }
    refreshing = true
    statusProcess.command = cli(["-j", "status"])
    statusProcess.running = true
  }

  function applyStatus(stdout, stderr, exitCode) {
    view = Model.parseStatus(stdout, stderr, exitCode)
    if (_desired !== -1 && view.on === (_desired === 1) && view.state !== Model.STATES.connecting) _desired = -1
    if (daemonUp && view.state !== Model.STATES.unregistered && !registrationProcess.running && !_registrationRead) {
      _registrationRead = true
      registrationProcess.command = cli(["-j", "registration", "show"])
      registrationProcess.running = true
    }
    if (!daemonUp || view.state === Model.STATES.unregistered) {
      _registrationRead = false
      organization = ""
      accountType = ""
    }
  }

  function connect() { runControl(["connect"], 1) }
  function disconnect() { runControl(["disconnect"], 0) }
  function toggle() {
    if (!canToggle) return
    if (on) disconnect()
    else connect()
  }

  // Registration against the organisation in the MDM file. warp-cli is
  // expected to open the browser itself; any URL it prints is opened too.
  // Runs apart from connect/disconnect, since it may wait for the browser.
  function enroll() { runBrowserFlow(["registration", "new"]) }

  // Force a fresh Cloudflare Access sign-in for Access-protected apps.
  function reauthenticate() { runBrowserFlow(["debug", "access-reauth"]) }

  function runControl(args, desired) {
    if (controlProcess.running || !daemonUp) return
    _desired = desired
    lastError = ""
    actionStatus = ""
    controlProcess.command = cli(args)
    controlProcess.running = true
  }

  function runBrowserFlow(args) {
    if (flowProcess.running || !daemonUp) return
    lastError = ""
    actionStatus = "Waiting for the browser sign-in…"
    flowProcess.urlOpened = false
    flowProcess.output = ""
    flowProcess.command = cli(args)
    flowProcess.running = true
    flowTimeout.restart()
  }

  function flowOutput(line) {
    flowProcess.output += line + "\n"
    var url = Model.firstUrl(line)
    if (url !== "" && !flowProcess.urlOpened) {
      flowProcess.urlOpened = true
      Qt.openUrlExternally(url)
      actionStatus = "Opened the sign-in page in your browser"
    }
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector { id: statusStdout; waitForEnd: true }
    stderr: StdioCollector { id: statusStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.refreshing = false
      root.applyStatus(String(statusStdout.text || ""), String(statusStderr.text || ""), exitCode)
    }
  }

  Process {
    id: registrationProcess
    running: false
    command: []
    stdout: StdioCollector { id: registrationStdout; waitForEnd: true }
    onExited: function(exitCode) {
      var reg = exitCode === 0 ? Model.parseRegistration(String(registrationStdout.text || "")) : null
      root.organization = reg ? reg.organization : ""
      root.accountType = reg ? reg.accountType : ""
    }
  }

  Process {
    id: controlProcess
    running: false
    command: []
    stdout: StdioCollector { id: controlStdout; waitForEnd: true }
    stderr: StdioCollector { id: controlStderr; waitForEnd: true }
    onExited: function(exitCode) {
      var out = String(controlStdout.text || "")
      var err = String(controlStderr.text || "")
      if (exitCode !== 0) {
        // Policy refusals (locked switch, always-on) land here.
        root._desired = -1
        root.lastError = Model.firstLine(err) || Model.firstLine(out) || ("warp-cli exited with " + exitCode)
      }
      settle.ticks = 0
      settle.restart()
      root.refresh()
    }
  }

  // Sign-in and re-auth: output is streamed so a printed URL opens at once,
  // and the flow is abandoned after five minutes.
  Process {
    id: flowProcess
    property bool urlOpened: false
    property string output: ""
    running: false
    command: []
    stdout: SplitParser { onRead: function(line) { root.flowOutput(line) } }
    stderr: SplitParser { onRead: function(line) { root.flowOutput(line) } }
    onExited: function(exitCode) {
      flowTimeout.stop()
      if (exitCode !== 0) root.lastError = Model.firstLine(flowProcess.output) || ("warp-cli exited with " + exitCode)
      else if (!flowProcess.urlOpened) root.actionStatus = ""
      root._registrationRead = false
      root.refresh()
    }
  }

  Timer {
    id: flowTimeout
    interval: 300000
    repeat: false
    onTriggered: {
      flowProcess.running = false
      root.actionStatus = ""
      root.lastError = "Sign-in did not finish within five minutes"
    }
  }

  // Event hint: any output means "something changed". If the listener keeps
  // dying (daemon down, or -l unsupported) it is retried with a doubling delay
  // up to a minute; the delay resets only once it has produced output or
  // stayed up for 30 s.
  Process {
    id: listenProcess
    property double startedAt: 0
    running: false
    command: root.cli(["-l", "status"])
    stdout: SplitParser {
      onRead: function(line) {
        listenRetry.interval = 1000
        changeDebounce.restart()
      }
    }
    onRunningChanged: if (running) startedAt = Date.now()
    onExited: function(exitCode) {
      if (Date.now() - listenProcess.startedAt > 30000) listenRetry.interval = 1000
      else listenRetry.interval = Math.min(60000, Math.max(2000, listenRetry.interval * 2))
      listenRetry.restart()
      root.refresh()
    }
  }

  Timer {
    id: listenRetry
    interval: 1000
    repeat: false
    onTriggered: if (root.listenForChanges && root.daemonUp) listenProcess.running = true
  }

  Timer {
    id: changeDebounce
    interval: 250
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: refreshAgain
    interval: 300
    repeat: false
    onTriggered: root.refresh()
  }

  // Poll faster while a connect/disconnect settles; the daemon may take a few
  // seconds (captive portal checks, MASQUE handshake).
  Timer {
    id: settle
    property int ticks: 0
    interval: 1000
    repeat: true
    onTriggered: {
      ticks += 1
      root.refresh()
      if (ticks >= 10 || root._desired === -1) {
        running = false
        root._desired = -1
      }
    }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  onDaemonUpChanged: if (daemonUp && listenForChanges && !listenProcess.running) listenRetry.restart()
  onListenForChangesChanged: if (!listenForChanges) listenProcess.running = false
  Component.onDestruction: {
    listenProcess.running = false
    flowProcess.running = false
  }
}
