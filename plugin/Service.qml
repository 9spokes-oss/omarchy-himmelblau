import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.Pam
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import "FingerprintModel.js" as FingerprintModel

Item {
  id: root

  property bool pinRecoveryAvailable: false
  property string lockWarning: ""

  property var shell: null
  property string omarchyPath: ""

  readonly property string home: Quickshell.env("HOME")
  readonly property string stateHome: home + "/.local/state"
  readonly property string userName: Quickshell.env("USER") || Quickshell.env("LOGNAME")
  readonly property string currentBackgroundLink: stateHome + "/omarchy/current/background"

  property bool lockRequested: false
  property bool pendingSessionLock: false
  property bool authenticatingPassword: false
  property bool fingerprintAuthenticating: false
  property bool passwordPamConfigured: false
  property bool fingerprintConfigured: false
  property int fingerprintUnreachedStreak: 0
  property bool fingerprintAttemptReachedDevice: false
  property bool fingerprintAttemptFastError: false
  property double fingerprintAttemptPromptedAtMs: 0
  property double fingerprintLastNudgeMs: 0
  property double fingerprintLastSettleMs: 0
  property double fingerprintResumedAtMs: 0
  property int fingerprintProbeStreak: 0
  // While the lock has the displays off no new attempt starts, so the reader
  // isn't held awake all night; any wake starts it again.
  property bool fingerprintPaused: false
  // The sleep watch is stopped while paused, so a sleep seen after the
  // pre-suspend lock request is marked here instead.
  property bool fingerprintSleptWhilePaused: false
  // When the armed retry is due, so time spent blank counts toward it.
  property double fingerprintRetryDueMs: 0
  // The backoff the armed retry was set to; a wake shortens the timer to the
  // remaining wait, so the input guard reads the tier from here instead.
  property int fingerprintRetryBackoffMs: 0
  // Outputs the lock last saw, so only a display arriving counts as a wake.
  property int knownScreenCount: 0
  property bool previewVisible: false
  property string enteredPassword: ""
  property var passwordPam: null
  property int passwordAttempt: 0
  property bool awaitingPasswordResponse: false
  property bool passwordResponseVisible: false
  property string passwordPrompt: ""
  property var passwordNotices: []
  property bool passwordMessageIsError: false
  property var pinRecovery: null
  property bool recoveringPIN: false
  property int recoveryAttempt: 0
  property string lastRecoveryResult: "none"
  property string failureMessage: ""
  property int failedAttempts: 0
  property string backgroundPath: ""
  property string videoPosterPath: ""
  property int backgroundVersion: 0
  // The wallpaper file's mtime and size. The lock caches its wallpaper by
  // version, so a file overwritten in place must bump the version too.
  property string backgroundSignature: ""
  property string lastEvent: "init"
  property string lastEventAt: ""
  readonly property bool videoBackground: Util.isVideoPath(backgroundPath)
  property bool strandedLock: false
  property bool strandedLockResolved: false

  readonly property bool locked: lockRequested || sessionLock.locked || sessionLock.secure

  // Decisions read the lock state afresh. A property binding that missed a change
  // notification once reported locked after an unlock, and a lock request before
  // suspend then took that for done and never locked the session.
  function currentlyLocked() {
    return lockRequested || sessionLock.locked || sessionLock.secure
  }
  readonly property bool authenticating: authenticatingPassword || fingerprintAuthenticating
  // A prompt clears the unavailable notice before the attempt finishes.
  readonly property bool fingerprintUnavailable: FingerprintModel.isUnavailable(fingerprintProbeStreak) || (fingerprintConfigured && (!fingerprintAttemptReachedDevice || fingerprintAttemptFastError) && FingerprintModel.isUnavailable(fingerprintUnreachedStreak))

  function realScreenCount() {
    var screens = Quickshell.screens || []
    var count = 0

    for (var i = 0; i < screens.length; i++) {
      var screen = screens[i]
      if (screen && screen.name && screen.width > 0 && screen.height > 0) count += 1
    }

    return count
  }

  function hasRealScreen() {
    return realScreenCount() > 0
  }

  function queueSessionLock() {
    pendingSessionLock = true
    if (!sessionLockStabilizeTimer.running) logEvent("lock-pending: screen-stabilizing")
    sessionLockStabilizeTimer.restart()
    if (!pendingSessionLockTimer.running) pendingSessionLockTimer.start()
  }

  function requestSessionLock() {
    if (!lockRequested || sessionLock.locked || sessionLock.secure) return
    if (sessionLockStabilizeTimer.running) return

    if (!hasRealScreen()) {
      if (!pendingSessionLock || lastEvent !== "lock-pending: no-real-screen") logEvent("lock-pending: no-real-screen")
      pendingSessionLock = true
      if (!pendingSessionLockTimer.running) pendingSessionLockTimer.start()
      return
    }

    pendingSessionLock = false
    pendingSessionLockTimer.stop()
    sessionLock.locked = true
  }

  // ext-session-lock outlives its client, and a restart carries no lock over, so
  // a session locked this early may be an orphan behind Hyprland's failsafe. It
  // may also be another shell's live lock, which Hyprland would let this one
  // take over and then strand on exit, so the helper answers undetermined while
  // another locker runs. Outputs are often still absent here, so ask until the
  // answer means something.
  function checkStrandedLock() {
    if (strandedLockResolved || strandedLockCheckProc.running) return

    // A lock this shell took is nobody's orphan.
    if (currentlyLocked()) {
      strandedLockResolved = true
      return
    }

    strandedLockCheckProc.running = true
  }

  function recoverStrandedLock() {
    if (!strandedLock || currentlyLocked() || !passwordPamConfigured) return

    strandedLock = false
    logEvent("lock-stranded: recovering")
    beginLock()
  }

  function refreshBackground() {
    if (!readlinkProc.running) readlinkProc.running = true
  }

  function refreshPoster() {
    if (!root.videoBackground) {
      root.videoPosterPath = ""
      return
    }
    if (posterProc.running) return
    posterProc.sourcePath = root.backgroundPath
    posterProc.running = true
  }

  function refreshFingerprintStatus() {
    if (!fingerprintCheckProc.running) fingerprintCheckProc.running = true
  }

  // Only definitive enrollment results may disable authentication.
  function applyFingerprintProbe(text) {
    var status = FingerprintModel.classifyProbe(text)
    if (status === "unknown") {
      if (fingerprintConfigured) return
      fingerprintProbeStreak += 1
      if (lockRequested) {
        fingerprintRecheckTimer.interval = FingerprintModel.retryDelayMs(fingerprintProbeStreak)
        fingerprintRecheckTimer.restart()
      }
      return
    }
    fingerprintProbeStreak = 0
    fingerprintRecheckTimer.stop()
    fingerprintConfigured = status === "yes"
    if (lockRequested && fingerprintConfigured) {
      // A pending retry already owns the next attempt.
      if (!fingerprintRetryTimer.running) startFingerprint()
    } else if (!fingerprintConfigured) {
      // abort() delivers no completion signal, so close the attempt here
      // too; settle returns before arming a retry while unconfigured.
      if (fingerprintPam.active) fingerprintPam.abort()
      settleFingerprintAttempt()
      fingerprintRetryTimer.stop()
    }
  }

  function logEvent(event) {
    lastEvent = event
    lastEventAt = new Date().toISOString()
    console.log("omarchy lock " + lastEventAt + " " + event)
  }

  function resetAuthenticationState() {
    enteredPassword = ""
    cancelPasswordAttempt()
    failureMessage = ""
    failedAttempts = 0
    authenticatingPassword = false
    fingerprintAuthenticating = false
    fingerprintUnreachedStreak = 0
    fingerprintAttemptFastError = false
    fingerprintAttemptPromptedAtMs = 0
    fingerprintLastNudgeMs = 0
    fingerprintLastSettleMs = 0
    fingerprintResumedAtMs = 0
    fingerprintProbeStreak = 0
    fingerprintPaused = false
    fingerprintSleptWhilePaused = false
    fingerprintRecheckTimer.stop()
    fingerprintRetryTimer.stop()
    fingerprintReachTimer.stop()
    fingerprintResumeTimer.stop()
    if (fingerprintPam.active) fingerprintPam.abort()
  }

  function refuseLock() {
    logEvent("lock-denied: missing-pam")
    if (!lockRefusedNotice.running) lockRefusedNotice.running = true
  }

  // Runs after locking: a setup problem is shown on the lock, never a reason to stay unlocked.
  function checkLockSetup() {
    if (!pinHelperCheck.running) pinHelperCheck.running = true
    if (lockPreflight.running) return
    lockWarning = ""
    lockPreflightTimeout.restart()
    lockPreflight.running = true
  }

  function warnLockSetup() {
    if (lockRequested) lockWarning = "Lock setup check failed. After unlocking, run: omarchy-himmelblau status"
  }

  function beginLock() {
    if (!passwordPamConfigured) {
      refuseLock()
      return false
    }

    resetAuthenticationState()
    lockRequested = true
    armBlankTimer()
    logEvent("lock-requested")
    queueSessionLock()
    checkLockSetup()

    Qt.callLater(function() {
      root.refreshBackground()
      root.refreshFingerprintStatus()
    })

    return true
  }

  function finishUnlock() {
    if (!root.currentlyLocked()) return

    lockRequested = false
    pendingSessionLock = false
    sessionLockStabilizeTimer.stop()
    pendingSessionLockTimer.stop()
    resetAuthenticationState()
    idleBlankTimer.stop()
    sessionLock.locked = false
    logEvent("unlocked")
    runWake()
  }

  function armBlankTimer(delayMs) {
    idleBlankTimer.interval = delayMs || idleBlankTimer.idleMs
    idleBlankTimer.armedAt = Date.now()
    idleBlankTimer.restart()
  }

  function runWake() {
    if (!wakeProcess.running) wakeProcess.running = true
    if (lockRequested) armBlankTimer()
    resumeFingerprint()
    nudgeFingerprint()
  }

  // An attempt already running is left to finish under its own timeouts, so
  // a slow claim still completes; only the next one waits for a wake.
  function pauseFingerprint() {
    if (fingerprintPaused) return
    fingerprintPaused = true
    fingerprintRetryTimer.stop()
  }

  // The retry keeps whatever backoff was armed, counting the time spent blank,
  // so a failing reader still gets its idle window; after a sleep it recovers
  // at once, as the sleep watch does.
  function resumeFingerprint() {
    if (!fingerprintPaused) return
    fingerprintPaused = false
    if (fingerprintSleptWhilePaused) {
      fingerprintSleptWhilePaused = false
      noteFingerprintResumed()
      fingerprintRetryBackoffMs = FingerprintModel.MATCH_RETRY_MS
      fingerprintRetryTimer.interval = FingerprintModel.MATCH_RETRY_MS
    } else {
      fingerprintRetryTimer.interval = Math.max(FingerprintModel.MATCH_RETRY_MS, fingerprintRetryDueMs - Date.now())
    }
    if (lockRequested && fingerprintConfigured && !fingerprintAuthenticating) fingerprintRetryTimer.restart()
  }

  // A resume can light the panel with no input to wake the lock, so after a
  // lock request the reader comes back once a sleep is seen; the blank
  // countdown then pauses it again if nothing else happens. The idle service
  // repeats the same request without sleeping, so the sleep has to be seen.
  function noteSleepRequested() {
    if (!lockRequested || !fingerprintPaused) return
    // logind holds a suspend for its inhibitor delay, at most 15 seconds here.
    fingerprintResumeTimer.ticksLeft = 20
    fingerprintResumeTimer.lastTickMs = Date.now()
    fingerprintResumeTimer.restart()
  }

  // User activity advances retries without recreating a busy retry loop.
  function nudgeFingerprint() {
    if (!lockRequested) return
    if (!fingerprintConfigured && fingerprintRecheckTimer.running) {
      var now = Date.now()
      if (FingerprintModel.shouldNudge(now, fingerprintLastNudgeMs, now - FingerprintModel.IDLE_CLEAR_MS, fingerprintRecheckTimer.interval)) {
        fingerprintLastNudgeMs = now
        fingerprintRecheckTimer.interval = FingerprintModel.MATCH_RETRY_MS
        fingerprintRecheckTimer.restart()
      }
      return
    }
    if (!fingerprintConfigured) return
    if (fingerprintPam.active || fingerprintAuthenticating) return
    if (!fingerprintRetryTimer.running) return
    var now = Date.now()
    if (!FingerprintModel.shouldNudge(now, fingerprintLastNudgeMs, fingerprintLastSettleMs, fingerprintRetryBackoffMs)) return
    fingerprintLastNudgeMs = now
    armFingerprintRetry(FingerprintModel.MATCH_RETRY_MS)
  }


  function armFingerprintRetry(delayMs) {
    fingerprintRetryTimer.interval = delayMs
    fingerprintRetryBackoffMs = delayMs
    fingerprintRetryDueMs = Date.now() + delayMs
    if (!fingerprintPaused) fingerprintRetryTimer.restart()
  }

  // Reset pre-sleep failures while the resume hook restarts fprintd.
  function noteFingerprintResumed() {
    var now = Date.now()
    if (FingerprintModel.inResumeGrace(now, fingerprintResumedAtMs)) return
    logEvent("fingerprint-resume: streak=" + fingerprintUnreachedStreak)
    fingerprintResumedAtMs = now
    fingerprintUnreachedStreak = 0
  }

  // A suspended PAM conversation may be orphaned by the daemon restart.
  function restartFingerprintAfterSleep() {
    noteFingerprintResumed()
    if (fingerprintAuthenticating || fingerprintPam.active) {
      if (fingerprintPam.active) fingerprintPam.abort()
      settleFingerprintAttempt()
      return
    }
    if (!fingerprintRetryTimer.running) return
    armFingerprintRetry(FingerprintModel.MATCH_RETRY_MS)
  }

  function runBlank() {
    pauseFingerprint()
    if (!blankProcess.running) blankProcess.running = true
  }

  function currentPasswordAttempt(context, attempt) {
    return authenticatingPassword && context === passwordPam && attempt === passwordAttempt
  }

  function clearPasswordPrompt() {
    enteredPassword = ""
    awaitingPasswordResponse = false
    passwordResponseVisible = false
    passwordPrompt = ""
  }

  function submitPassword(value) {
    if (!lockRequested) return
    if (pinRecovery) {
      if (!recoveringPIN || !awaitingPasswordResponse || !pinRecovery.running) return
      var response = String(value || "")
      clearPasswordPrompt()
      try {
        var encoded = encodeURIComponent(response)
        if (encoded.length > 12288) { stopPinRecovery("failed"); return }
        pinRecovery.write("response " + encoded + "\n")
      } catch (error) { stopPinRecovery("failed") }
      return
    }
    if (authenticatingPassword) {
      var context = passwordPam
      if (!context || !context.active || !awaitingPasswordResponse || !context.responseRequired) return
      var response = String(value || "")
      clearPasswordPrompt()
      context.respond(response)
      return
    }
    // The idle field is read-only: begin first, then type into the actual PAM prompt.
    if (passwordPam || String(value || "").length > 0) return
    runWake()
    clearPasswordPrompt()
    passwordNotices = []
    passwordMessageIsError = false
    failureMessage = ""
    authenticatingPassword = true
    passwordAttempt += 1
    var context = passwordPamFactory.createObject(root, { attemptId: passwordAttempt })
    passwordPam = context
    if (!context || !context.start()) handlePasswordFailure(context, passwordAttempt)
  }

  function handlePasswordMessage(context, attempt) {
    if (!currentPasswordAttempt(context, attempt) || !context.active) return
    clearPasswordPrompt()
    passwordMessageIsError = context.messageIsError
    var message = String(context.message || "")
    if (context.responseRequired) {
      passwordPrompt = message
      passwordResponseVisible = context.responseVisible
      awaitingPasswordResponse = true
    } else if (message.length > 0) {
      var notices = passwordNotices.slice()
      if (notices.length === 0 || notices[notices.length - 1] !== message) {
        if (notices.length < 8) notices.push(message)
        else notices[7] = message
        passwordNotices = notices
      }
    }
    runWake()
  }

  function settlePasswordAttempt(context, attempt, success, cancelled) {
    if (!currentPasswordAttempt(context, attempt)) return
    authenticatingPassword = false
    passwordPam = null
    clearPasswordPrompt()
    passwordNotices = []
    passwordMessageIsError = false
    // Clear identity before abort/destroy: reentrant or late signals cannot unlock.
    if (context) {
      if (context.active) context.abort()
      context.destroy()
    }
    if (!lockRequested) return
    if (success) {
      finishUnlock()
    } else if (!cancelled) {
      failedAttempts += 1
      failureMessage = I18n.t("lock.authenticationFailed").arg(I18n.formatNumber(failedAttempts))
      runWake()
    } else {
      failureMessage = ""
      runWake()
    }
  }

  function handlePasswordFailure(context, attempt) {
    settlePasswordAttempt(context, attempt, false, false)
  }

  function cancelPasswordAttempt() {
    if (pinRecovery) { stopPinRecovery("cancelled"); return }
    if (authenticatingPassword) settlePasswordAttempt(passwordPam, passwordAttempt, false, true)
    else clearPasswordPrompt()
  }

  function completePasswordAttempt(context, attempt, result) {
    settlePasswordAttempt(context, attempt, result === PamResult.Success, false)
  }

  function beginPinRecovery() {
    if (!lockRequested || authenticatingPassword || passwordPam || pinRecovery) return
    if (!pinRecoveryAvailable) {
      lastRecoveryResult = "failed"
      failureMessage = "PIN recovery helper unavailable; normal sign-in remains available."
      return
    }
    clearPasswordPrompt()
    passwordNotices = []
    passwordMessageIsError = false
    failureMessage = ""
    lastRecoveryResult = "none"
    recoveryAttempt += 1
    recoveringPIN = true
    authenticatingPassword = true
    var context = pinRecoveryFactory.createObject(root, { attemptId: recoveryAttempt })
    pinRecovery = context
    if (!context) { recoveringPIN = false; authenticatingPassword = false; lastRecoveryResult = "failed"; return }
    context.running = true
  }

  function currentPinRecovery(context, attempt) {
    return recoveringPIN && context === pinRecovery && attempt === recoveryAttempt
  }

  function handlePinRecoveryLine(context, attempt, line) {
    if (!currentPinRecovery(context, attempt)) return
    if (line.length > 20000 || ++context.messageCount > 256) { stopPinRecovery("failed"); return }
    var message
    try { message = JSON.parse(line) } catch (error) { stopPinRecovery("failed"); return }
    if (!message || typeof message !== "object" || Array.isArray(message) || context.resultReceived) { stopPinRecovery("failed"); return }
    if (message.type === "result" && typeof message.success === "boolean") {
      clearPasswordPrompt()
      context.resultReceived = true
      context.reportedSuccess = message.success
      return
    }
    if ((message.type !== "prompt" && message.type !== "info" && message.type !== "error") || typeof message.text !== "string" || message.text.length > 8192) {
      stopPinRecovery("failed")
      return
    }
    clearPasswordPrompt()
    passwordMessageIsError = message.type === "error"
    if (message.type === "prompt") {
      if (typeof message.visible !== "boolean") { stopPinRecovery("failed"); return }
      passwordPrompt = message.text
      passwordResponseVisible = message.visible
      awaitingPasswordResponse = true
    } else {
      var notices = passwordNotices.slice()
      if (notices.length === 0 || notices[notices.length - 1] !== message.text) {
        if (notices.length < 8) notices.push(message.text)
        else notices[7] = message.text
        passwordNotices = notices
      }
    }
    runWake()
  }

  function stopPinRecovery(result) {
    var context = pinRecovery
    if (!context) return
    // Invalidate before killing, and retain the process until it has exited.
    recoveringPIN = false
    recoveryAttempt += 1
    lastRecoveryResult = result
    clearPasswordPrompt()
    passwordNotices = ["PIN change stopped; it may already have completed. Sign in to check."]
    passwordMessageIsError = result === "failed"
    if (context.running) context.signal(9)
    else finishPinRecovery(context, context.attemptId, 1)
  }

  function finishPinRecovery(context, attempt, exitCode) {
    if (context !== pinRecovery) return
    if (currentPinRecovery(context, attempt)) {
      lastRecoveryResult = exitCode === 0 && context.reportedSuccess ? "success" : "failed"
      passwordNotices = [lastRecoveryResult === "success" ? "PIN updated; sign in with the new PIN." : "PIN change failed; sign in to check or retry recovery."]
      passwordMessageIsError = lastRecoveryResult !== "success"
    }
    recoveringPIN = false
    pinRecovery = null
    authenticatingPassword = false
    clearPasswordPrompt()
    context.destroy()
    // PIN replacement success NEVER unlocks. A new authentication is required.
    runWake()
  }

  function startFingerprint() {
    if (!lockRequested || !sessionLock.secure || !fingerprintConfigured || fingerprintPaused) return
    if (fingerprintPam.active || fingerprintAuthenticating) return

    fingerprintAuthenticating = true
    fingerprintAttemptReachedDevice = false
    fingerprintAttemptFastError = false
    fingerprintAttemptPromptedAtMs = 0
    if (!fingerprintPam.start()) {
      // Pace a failed start while checking whether its PAM configuration was
      // removed; a definitive "no" stops retries and hides the indicator.
      settleFingerprintAttempt()
      refreshFingerprintStatus()
      return
    }
    // Bound claims that never prompt; a normal verify waits for a finger
    // under pam_fprintd's own timeout after reaching the reader.
    fingerprintReachTimer.restart()
  }

  // A prompt proves the claim landed, so stop waiting for reachability.
  function noteFingerprintReachedDevice() {
    if (fingerprintAttemptReachedDevice) return
    fingerprintAttemptReachedDevice = true
    fingerprintAttemptPromptedAtMs = Date.now()
    fingerprintReachTimer.stop()
  }

  // abort() gives no completion signal; settle the attempt here.
  function timeoutFingerprintReach() {
    logEvent("fingerprint-reach-timeout")
    if (fingerprintPam.active) fingerprintPam.abort()
    settleFingerprintAttempt()
  }

  // onError and onCompleted can both fire; settle each attempt once.
  function settleFingerprintAttempt(deviceError) {
    if (!fingerprintAuthenticating) return
    fingerprintAuthenticating = false
    fingerprintReachTimer.stop()
    if (!lockRequested || !fingerprintConfigured) return

    // An error can arrive before the sleep watcher notices the wall-clock gap.
    var now = Date.now()
    fingerprintAttemptFastError = !!deviceError && fingerprintAttemptReachedDevice && now - fingerprintAttemptPromptedAtMs < FingerprintModel.FAST_ERROR_MS
    var usableAttempt = fingerprintAttemptReachedDevice && !fingerprintAttemptFastError
    if (!usableAttempt && fingerprintSleepWatch.running
        && FingerprintModel.spannedSleep(now - fingerprintSleepWatch.lastTickMs, fingerprintSleepWatch.interval)) {
      noteFingerprintResumed()
    }

    // Reached attempts are the steady state (one per swipe window), so only
    // the misses and the recovery from them leave a trace.
    var previousStreak = fingerprintUnreachedStreak
    var inGrace = FingerprintModel.inResumeGrace(now, fingerprintResumedAtMs)
    fingerprintUnreachedStreak = FingerprintModel.nextStreak(previousStreak, usableAttempt, inGrace)
    if (!usableAttempt) {
      var crossed = !FingerprintModel.isUnavailable(previousStreak) && FingerprintModel.isUnavailable(fingerprintUnreachedStreak)
      logEvent((crossed ? "fingerprint-unavailable" : "fingerprint-unreached") + ": streak=" + fingerprintUnreachedStreak)
    } else if (previousStreak > 0) {
      logEvent("fingerprint-recovered: streak=" + previousStreak)
    }
    fingerprintLastSettleMs = now
    armFingerprintRetry(FingerprintModel.retryDelayMs(fingerprintUnreachedStreak))
  }

  function handleFingerprintFinished(result) {
    if (result === PamResult.Success && lockRequested) {
      // A match after a run of misses is the recovery too; the unlock resets
      // the streak without settling, so log it here or it leaves no trace.
      if (fingerprintUnreachedStreak > 0) logEvent("fingerprint-recovered: streak=" + fingerprintUnreachedStreak)
      finishUnlock()
    } else {
      settleFingerprintAttempt(result === PamResult.Error)
    }
  }

  WlSessionLock {
    id: sessionLock

    locked: false

    onSecureStateChanged: {
      root.logEvent("secure=" + secure)
      if (secure) {
        root.pendingSessionLock = false
        sessionLockStabilizeTimer.stop()
        pendingSessionLockTimer.stop()
        root.startFingerprint()
      }
    }

    onLockStateChanged: {
      root.logEvent("session-locked=" + locked)

      if (locked) {
        root.pendingSessionLock = false
        sessionLockStabilizeTimer.stop()
        pendingSessionLockTimer.stop()
      }

      if (!locked && root.lockRequested) {
        root.lockRequested = false
        root.pendingSessionLock = false
        sessionLockStabilizeTimer.stop()
        pendingSessionLockTimer.stop()
        root.resetAuthenticationState()
        root.runWake()
      }
    }

    WlSessionLockSurface {
      id: lockSurface
      // Right-to-left languages read this window mirrored.
      Binding { target: lockSurface.contentItem; property: "LayoutMirroring.enabled"; value: Commons.I18n.rightToLeft }
      Binding { target: lockSurface.contentItem; property: "LayoutMirroring.childrenInherit"; value: true }

      color: Commons.Color.background

      LockView {
        id: lockView
        anchors.fill: parent
        backgroundPath: root.backgroundPath
        videoPosterPath: root.videoPosterPath
        backgroundVersion: root.backgroundVersion
        fingerprintConfigured: root.fingerprintConfigured || root.fingerprintUnavailable
        fingerprintUnavailable: root.fingerprintUnavailable
        authenticatingPassword: root.authenticatingPassword
        awaitingResponse: root.awaitingPasswordResponse
        responseVisible: root.passwordResponseVisible
        conversationMessage: root.passwordNotices.join("\n\n")
        promptMessage: root.passwordPrompt
        warningMessage: root.lockWarning
        onRecoveryRequested: root.beginPinRecovery()
        conversationError: root.passwordMessageIsError
        onCancelRequested: root.cancelPasswordAttempt()
        failureMessage: root.failureMessage
        failedAttempts: root.failedAttempts
        inputEnabled: root.lockRequested
        loadBackground: root.locked
        passwordText: root.enteredPassword
        onPasswordTextEdited: function(password) { root.enteredPassword = password }
        onSubmitPassword: function(password) { root.submitPassword(password) }
        onClearFailureRequested: root.failureMessage = ""
        onWakeRequested: root.runWake()
      }

    }
  }

  PanelWindow {
    id: previewWindow
    // Right-to-left languages read this window mirrored.
    Binding { target: previewWindow.contentItem; property: "LayoutMirroring.enabled"; value: Commons.I18n.rightToLeft }
    Binding { target: previewWindow.contentItem; property: "LayoutMirroring.childrenInherit"; value: true }

    visible: root.previewVisible
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omarchy-lock-preview"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    LockView {
      anchors.fill: parent
      backgroundPath: root.backgroundPath
      videoPosterPath: root.videoPosterPath
      backgroundVersion: root.backgroundVersion
      fingerprintConfigured: root.fingerprintConfigured || root.fingerprintUnavailable
      fingerprintUnavailable: root.fingerprintUnavailable
      authenticatingPassword: false
      failureMessage: ""
      failedAttempts: 0
      inputEnabled: false
      loadBackground: root.previewVisible
      passwordText: ""
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onClicked: root.previewVisible = false
    }
  }

  Component {
    id: passwordPamFactory
    PamContext {
      id: conversation
      property int attemptId: 0
      config: "omarchy-himmelblau-lock-password"
      user: root.userName
      onPamMessage: root.handlePasswordMessage(conversation, attemptId)
      onCompleted: function(result) { root.completePasswordAttempt(conversation, attemptId, result) }
      onError: function(error) { root.handlePasswordFailure(conversation, attemptId) }
    }
  }

  Component {
    id: pinRecoveryFactory
    Process {
      id: recovery
      property int attemptId: 0
      property bool startedOnce: false
      property bool reportedSuccess: false
      property bool resultReceived: false
      property int messageCount: 0
      command: ["/usr/lib/omarchy-himmelblau/pin-change"]
      stdinEnabled: true
      stdout: SplitParser { onRead: function(data) { root.handlePinRecoveryLine(recovery, attemptId, data) } }
      onStarted: startedOnce = true
      onRunningChanged: if (!running && !startedOnce) root.finishPinRecovery(recovery, attemptId, 1)
      onExited: function(exitCode) { root.finishPinRecovery(recovery, attemptId, exitCode) }
    }
  }

  PamContext {
    id: fingerprintPam
    config: "omarchy-lock-fingerprint"
    user: root.userName

    onPamMessage: {
      if (!messageIsError) root.noteFingerprintReachedDevice()
    }

    onCompleted: function(result) {
      root.handleFingerprintFinished(result)
    }

    onError: function(error) {
      root.settleFingerprintAttempt(true)
    }
  }

  // The lock only starts decoding its wallpaper once locked, and a machine
  // suspending right after locking froze that decode partway: waking showed
  // the password field on a bare background, then the wallpaper popped in.
  // Keep each screen's lock wallpaper decoded in the image cache ahead of
  // time, as the lock view requests it (same URL, the screen's logical size,
  // PreserveAspectCrop), so the lock draws it on its first frame.
  readonly property string lockWallpaperPath: videoBackground ? videoPosterPath : backgroundPath
  readonly property string lockWallpaperUrl: lockWallpaperPath && !Util.isVideoPath(lockWallpaperPath)
    ? Util.fileUrl(lockWallpaperPath) + (backgroundVersion ? "?v=" + backgroundVersion : "")
    : ""

  Variants {
    model: Quickshell.screens

    Image {
      required property var modelData
      visible: false
      source: root.lockWallpaperUrl
      sourceSize.width: modelData.width
      sourceSize.height: modelData.height
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      cache: true
    }
  }

  Timer {
    id: fingerprintRetryTimer
    interval: FingerprintModel.MATCH_RETRY_MS
    repeat: false
    onTriggered: root.startFingerprint()
  }

  // Detect resume both during an active attempt and during backoff.
  Timer {
    id: fingerprintSleepWatch
    interval: 1000
    repeat: true
    running: root.lockRequested && root.fingerprintConfigured && (!root.fingerprintPaused || root.fingerprintAuthenticating)
    property double lastTickMs: 0
    onRunningChanged: lastTickMs = Date.now()
    onTriggered: {
      var now = Date.now()
      var slept = FingerprintModel.spannedSleep(now - lastTickMs, interval)
      lastTickMs = now
      if (slept) root.restartFingerprintAfterSleep()
    }
  }

  // Timers stand still through a suspend, so the first tick after a resume
  // sees the wall clock jump. Runs only for the seconds after a lock request
  // that a suspend can take to begin.
  Timer {
    id: fingerprintResumeTimer
    interval: 1000
    repeat: true
    property int ticksLeft: 0
    property double lastTickMs: 0
    onTriggered: {
      var now = Date.now()
      var slept = FingerprintModel.spannedSleep(now - lastTickMs, interval)
      lastTickMs = now
      if (slept && root.lockRequested && root.fingerprintPaused) {
        stop()
        root.fingerprintSleptWhilePaused = true
        root.resumeFingerprint()
        root.armBlankTimer(idleBlankTimer.resumeMs)
      } else if (slept || !root.fingerprintPaused || --ticksLeft <= 0) {
        stop()
      }
    }
  }

  Timer {
    id: fingerprintReachTimer
    interval: FingerprintModel.REACH_TIMEOUT_MS
    repeat: false
    onTriggered: root.timeoutFingerprintReach()
  }

  Process {
    id: readlinkProc
    command: ["bash", "-c", "path=$(readlink -f -- \"$1\") && printf '%s\\n%s\\n' \"$path\" \"$(stat -Lc %Y:%s -- \"$path\" 2>/dev/null)\"", "_", root.currentBackgroundLink]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").split("\n")
        var next = String(lines[0] || "").trim()
        var signature = String(lines[1] || "").trim()
        if (next !== root.backgroundPath) {
          root.videoPosterPath = ""
          root.backgroundPath = next
          root.backgroundSignature = signature
          root.backgroundVersion += 1
        } else if (signature !== root.backgroundSignature) {
          root.backgroundSignature = signature
          root.backgroundVersion += 1
        }
        root.refreshPoster()
      }
    }
  }

  Process {
    id: posterProc
    property string sourcePath: ""
    command: ["/usr/bin/bash", decodeURIComponent(String(Qt.resolvedUrl("poster.sh")).replace("file://", "")), sourcePath]
    stdout: StdioCollector { id: posterOutput; waitForEnd: true }
    onExited: function(exitCode) {
      if (sourcePath !== root.backgroundPath) {
        root.refreshPoster()
      } else {
        root.videoPosterPath = exitCode === 0 ? String(posterOutput.text || "").trim() : ""
      }
    }
  }

  // Keep fprintd errors distinguishable from an explicit empty enrollment.
  Process {
    id: fingerprintCheckProc
    command: ["bash", "-c", "if [[ -f /etc/pam.d/omarchy-lock-fingerprint ]] && command -v fprintd-list >/dev/null 2>&1; then LC_ALL=C fprintd-list \"$USER\" 2>&1; else echo no; fi"]
    stdout: StdioCollector { id: fingerprintCheckStdout; waitForEnd: true }
    onExited: root.applyFingerprintProbe(fingerprintCheckStdout.text)
  }

  // Retries a probe that could not reach fprintd, paced like the attempt
  // retries so a daemon that stays unreachable is asked about ever less often.
  Timer {
    id: fingerprintRecheckTimer
    interval: FingerprintModel.ERROR_RETRY_BASE_MS
    repeat: false
    onTriggered: root.refreshFingerprintStatus()
  }

  Process {
    id: strandedLockCheckProc
    // $PPID is this shell: a crash re-execs in place, so its pid is the one to skip.
    command: ["bash", "-c", "omarchy-hyprland-session-lock-stranded \"$PPID\""]
    onExited: function(exitCode) {
      // No output to read the lock off yet.
      if (exitCode === 2) return

      root.strandedLockResolved = true

      // A lock taken while this was in flight is this shell's own.
      root.strandedLock = exitCode === 0 && !root.currentlyLocked()
      root.recoverStrandedLock()
    }
  }

  Process {
    id: wakeProcess
    command: ["bash", "-c", "omarchy-system-wake"]
  }

  Process {
    id: blankProcess
    command: ["bash", "-c", "omarchy-brightness-keyboard off; omarchy-brightness-display off"]
  }

  Timer {
    id: idleBlankTimer
    readonly property int idleMs: 5000
    // A resume reaches this as soon as the kernel thaws, seconds before the
    // panel lights (on NVIDIA it waits out a VT switch and a modeset), so the
    // idle run-up blanked the lock just as it appeared.
    readonly property int resumeMs: 15000
    interval: idleMs
    repeat: false
    property double armedAt: 0
    onTriggered: {
      // A countdown frozen by suspend fires right after resume, which would
      // blank the freshly woken unlock screen under the user. Wall-clock time
      // exposes the gap: take a fresh run-up instead of blanking.
      if (Date.now() - armedAt > interval + 2000) {
        root.armBlankTimer(resumeMs)
        return
      }
      // Only a password check in flight should hold the display up. The
      // fingerprint PAM stays armed for the whole lock, so gating on
      // `authenticating` here would keep the panel lit until unlock.
      if (root.lockRequested && !root.authenticatingPassword) root.runBlank()
    }
  }

  Timer {
    id: sessionLockStabilizeTimer
    interval: 500
    repeat: false
    onTriggered: root.requestSessionLock()
  }

  Timer {
    id: pendingSessionLockTimer
    interval: 100
    repeat: true
    onTriggered: root.requestSessionLock()
  }

  Timer {
    id: strandedLockRetryTimer
    interval: 500
    repeat: true
    // Covers the compositor settling; screens coming back re-arm it.
    readonly property int budget: 20
    property int remaining: 20
    running: !root.strandedLockResolved && remaining > 0

    function rearm() {
      if (!root.strandedLockResolved) remaining = budget
    }

    onTriggered: {
      remaining -= 1
      root.checkStrandedLock()
    }
  }

  Connections {
    target: Quickshell
    function onScreensChanged() {
      var screens = root.realScreenCount()
      if (screens > root.knownScreenCount) {
        root.resumeFingerprint()
        if (root.lockRequested) root.armBlankTimer()
      }
      root.knownScreenCount = screens
      root.requestSessionLock()

      // A monitor still coming up has no workspace, so cannot answer yet.
      strandedLockRetryTimer.rearm()
      root.checkStrandedLock()
    }
  }

  onAuthenticatingPasswordChanged: {
    if (!lockRequested) return
    if (authenticatingPassword) idleBlankTimer.stop()
    else armBlankTimer()
  }

  FileView {
    path: "/etc/pam.d/omarchy-himmelblau-lock-password"
    watchChanges: true
    printErrors: false
    onLoaded: root.passwordPamConfigured = true
    onLoadFailed: root.passwordPamConfigured = false
    onFileChanged: reload()
  }

  Process {
    id: lockRefusedNotice
    command: ["notify-send", "-u", "critical", "Screen not locked", "Missing /etc/pam.d/omarchy-himmelblau-lock-password"]
  }

  Process {
    id: lockPreflight
    command: ["/usr/bin/omarchy-himmelblau", "locker-preflight"]
    stdout: SplitParser { onRead: function(data) {} }
    stderr: SplitParser { onRead: function(data) {} }
    onExited: function(exitCode) {
      lockPreflightTimeout.stop()
      if (exitCode !== 0) root.warnLockSetup()
    }
  }

  Timer {
    id: lockPreflightTimeout
    interval: 15000
    onTriggered: {
      root.warnLockSetup()
      lockPreflight.signal(15)
    }
  }

  Process {
    id: pinHelperCheck
    command: ["/usr/bin/test", "-x", "/usr/lib/omarchy-himmelblau/pin-change"]
    onExited: function(exitCode) { root.pinRecoveryAvailable = exitCode === 0 }
  }

  // No lock before PAM is known good. An answer from before then may be stale --
  // the failsafe can be cleared from a TTY -- so re-ask rather than act on it.
  onPasswordPamConfiguredChanged: {
    if (!passwordPamConfigured) return

    strandedLock = false
    strandedLockResolved = false
    strandedLockRetryTimer.rearm()
    checkStrandedLock()
  }

  Component.onCompleted: {
    knownScreenCount = realScreenCount()
    refreshBackground()
    refreshFingerprintStatus()
    checkStrandedLock()
  }

  ShellIpc {
    target: "lock"

    function lock(): string {
      if (!root.passwordPamConfigured) {
        root.refuseLock()
        return "missing-pam"
      }
      // omarchy-system-sleep-lock asks for the lock before every suspend.
      if (root.currentlyLocked()) root.noteSleepRequested()
      if (!root.currentlyLocked() && !root.beginLock()) return "failed"
      return "ok"
    }

    function isLocked(): string {
      return root.currentlyLocked() ? "true" : "false"
    }

    function status(): string {
      return JSON.stringify({
        locked: root.currentlyLocked(),
        requested: root.lockRequested,
        pending: root.pendingSessionLock,
        sessionLocked: sessionLock.locked,
        secure: sessionLock.secure,
        realScreens: root.realScreenCount(),
        passwordPam: root.passwordPamConfigured,
        lockWarning: root.lockWarning,
        recoveryAvailable: root.pinRecoveryAvailable,
        fingerprint: root.fingerprintConfigured,
        fingerprintUnavailable: root.fingerprintUnavailable,
        authenticating: root.authenticating,
        recoveryActive: root.pinRecovery !== null,
        lastRecoveryResult: root.lastRecoveryResult,
        lastEvent: root.lastEvent,
        lastEventAt: root.lastEventAt
      })
    }

    function preview(): string {
      root.refreshBackground()
      root.refreshFingerprintStatus()
      root.previewVisible = true
      return "ok"
    }

    function hidePreview(): string {
      root.previewVisible = false
      return "ok"
    }
  }
}
