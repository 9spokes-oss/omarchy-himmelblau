import assert from 'node:assert/strict';
import { loadFunctions, patchedFixture } from './qml-functions.mjs';

const service = patchedFixture('Service.qml', 'lock-conversation.patch');
const view = patchedFixture('LockView.qml', 'lock-view.patch');

const CONVERSATION_FUNCTIONS = [
  'currentPasswordAttempt', 'clearPasswordPrompt', 'submitPassword', 'handlePasswordMessage',
  'addPasswordNotice', 'settlePasswordAttempt', 'handlePasswordFailure', 'cancelPasswordAttempt',
  'completePasswordAttempt', 'beginPinRecovery', 'currentPinRecovery', 'handlePinRecoveryLine',
  'stopPinRecovery', 'escalatePinRecoveryStop', 'finishPinRecovery', 'currentlyLocked',
  'resetAuthenticationState', 'finishUnlock',
];

function stoppedTimer() {
  return { running: false, restart() { this.running = true; }, stop() { this.running = false; } };
}

// A locked lock service with fake PamContext and PIN helper factories.
function harness() {
  const state = {
    lockRequested: true,
    pinRecoveryAvailable: true,
    authenticatingPassword: false,
    passwordPam: null,
    passwordAttempt: 0,
    awaitingPasswordResponse: false,
    passwordResponseVisible: false,
    passwordPrompt: '',
    passwordNotices: [],
    passwordMessageIsError: false,
    pinRecovery: null,
    recoveringPIN: false,
    recoveryAttempt: 0,
    lastRecoveryResult: 'none',
    pinRecoverySignal: 15,
    recoveryBackoffMs: 0,
    recoveryRetryAtMs: 0,
    enteredPassword: '',
    failureMessage: '',
    failedAttempts: 0,
    now: 1e6,
    unlocks: 0,
    contexts: [],
    sessionLock: { locked: true, secure: true },
    fingerprintPam: { active: false },
    pinRecoveryStopTimer: stoppedTimer(),
    PamResult: { Success: 0 },
    I18n: { t() { return { arg(n) { return `failed ${n}`; } }; }, formatNumber(n) { return n; } },
    runWake() {},
    logEvent(event) { if (event === 'unlocked') state.unlocks++; },
  };
  state.root = state;
  state.Date = { now() { return state.now; } };
  for (const name of ['sessionLockStabilizeTimer', 'pendingSessionLockTimer', 'idleBlankTimer',
    'fingerprintRecheckTimer', 'fingerprintRetryTimer', 'fingerprintReachTimer', 'fingerprintResumeTimer']) {
    state[name] = stoppedTimer();
  }
  state.passwordPamFactory = {
    createObject(parent, properties) {
      const context = {
        ...properties,
        active: false,
        responseRequired: false,
        responseVisible: false,
        message: '',
        messageIsError: false,
        replies: [],
        start() { this.active = true; return true; },
        respond(response) { this.replies.push(response); },
        abort() {
          this.active = false;
          state.completePasswordAttempt(this, this.attemptId, 0);
          state.handlePasswordFailure(this, this.attemptId);
        },
        destroy() { this.destroyed = true; },
        prompt(message, visible = false) {
          Object.assign(this, { message, responseRequired: true, responseVisible: visible, messageIsError: false });
          state.handlePasswordMessage(this, this.attemptId);
        },
        info(message, error = false) {
          Object.assign(this, { message, responseRequired: false, messageIsError: error });
          state.handlePasswordMessage(this, this.attemptId);
        },
        complete(result) {
          this.active = false;
          state.completePasswordAttempt(this, this.attemptId, result);
        },
      };
      state.contexts.push(context);
      return context;
    },
  };
  state.pinRecoveryFactory = {
    createObject(parent, properties) {
      return {
        ...properties,
        running: false,
        stdinEnabled: true,
        reportedSuccess: false,
        messageCount: 0,
        writes: [],
        signals: [],
        write(text) { this.writes.push(text); },
        signal(number) { this.signals.push(number); },
        destroy() { this.destroyed = true; },
        line(message) { state.handlePinRecoveryLine(this, this.attemptId, JSON.stringify(message)); },
        exit(code) {
          this.running = false;
          state.finishPinRecovery(this, this.attemptId, code);
        },
      };
    },
  };
  return loadFunctions(service, CONVERSATION_FUNCTIONS, state);
}

let s, c, next, recovery;

// The idle field only starts a conversation; each prompt gets one fresh answer.
s = harness();
s.submitPassword('must-not-silently-discard');
assert.equal(s.contexts.length, 0);
s.submitPassword('');
c = s.passwordPam;
assert.equal(c.replies.length, 0);
c.prompt('PIN:');
assert.equal(s.awaitingPasswordResponse, true);
assert.equal(s.passwordResponseVisible, false);
s.submitPassword(''); // A stray Enter on a hidden prompt would be a faillock failure.
assert.equal(c.replies.length, 0);
assert.equal(s.awaitingPasswordResponse, true);
s.enteredPassword = 'dummy-pin';
s.submitPassword(s.enteredPassword);
assert.deepEqual(c.replies, ['dummy-pin']);
assert.equal(s.enteredPassword, '');
s.submitPassword('must-not-replay'); // Quickshell keeps responseRequired true.
assert.equal(c.replies.length, 1);
c.complete(0);
assert.equal(s.unlocks, 1);

// Password, MFA notices, visible prompts (empty answers allowed), OTP.
s = harness();
s.submitPassword('');
c = s.passwordPam;
c.prompt('Password:');
s.submitPassword('dummy-password');
c.info('Enter number 42 in Microsoft Authenticator');
assert.ok(s.passwordNotices.includes('Enter number 42 in Microsoft Authenticator'));
for (let i = 0; i < 20; i++) c.info('Waiting for approval');
assert.equal(c.replies.length, 1);
assert.equal(s.awaitingPasswordResponse, false);
c.prompt('Continue?', true);
s.submitPassword('');
c.prompt('One-time code:', true);
assert.equal(s.passwordResponseVisible, true);
s.submitPassword('dummy-otp');
assert.deepEqual(c.replies, ['dummy-password', '', 'dummy-otp']);
assert.equal(s.unlocks, 0);
c.complete(0);
assert.equal(s.unlocks, 1);

// PIN enrollment failure, late callbacks, cancel and retry.
s = harness();
s.submitPassword('');
c = s.passwordPam;
for (const prompt of ['New PIN:', 'Confirm PIN:']) {
  c.prompt(prompt);
  assert.equal(s.enteredPassword, '');
  assert.equal(s.passwordResponseVisible, false);
  s.submitPassword('dummy-new-pin');
}
assert.deepEqual(c.replies, ['dummy-new-pin', 'dummy-new-pin']);
c.info('PIN enrollment failed', true);
assert.equal(s.passwordMessageIsError, true);
c.complete(1);
assert.equal(s.unlocks, 0);
assert.equal(s.failedAttempts, 1);
c.complete(0);
assert.equal(s.unlocks, 0);
s.handlePasswordFailure(c, c.attemptId);
assert.equal(s.failedAttempts, 1);
s.submitPassword('');
next = s.passwordPam;
assert.notEqual(next, c);
c.prompt('stale');
c.complete(0);
assert.equal(s.passwordPam, next);
assert.equal(s.unlocks, 0);
next.prompt('PIN:');
s.cancelPasswordAttempt();
assert.equal(s.passwordPam, null);
assert.equal(s.enteredPassword, '');
assert.equal(s.passwordNotices.length, 0);
assert.equal(s.unlocks, 0);
assert.equal(s.failedAttempts, 1);
s.submitPassword('');
const last = s.passwordPam;
next.complete(0);
assert.equal(s.passwordPam, last);
assert.equal(s.unlocks, 0);
last.prompt('PIN:');
s.submitPassword('dummy-pin');
last.complete(0);
assert.equal(s.unlocks, 1);

// Fingerprint success: finishUnlock cancels the password attempt before abort callbacks.
s = harness();
s.submitPassword('');
c = s.passwordPam;
c.prompt('PIN:');
s.finishUnlock();
assert.equal(s.unlocks, 1);
assert.equal(c.destroyed, true);
assert.equal(s.passwordPam, null);
assert.equal(s.enteredPassword, '');
c.complete(0);
c.prompt('late');
assert.equal(s.unlocks, 1);
assert.equal(s.failedAttempts, 0);
s.lockRequested = true;
s.sessionLock.locked = true;
s.submitPassword('');
next = s.passwordPam;
c.complete(0);
assert.equal(s.passwordPam, next);
assert.equal(s.unlocks, 1);

// Allocation or start failure settles without unlocking.
s = harness();
s.passwordPamFactory.createObject = () => null;
s.submitPassword('');
assert.equal(s.unlocks, 0);
assert.equal(s.failedAttempts, 1);
assert.equal(s.authenticatingPassword, false);
s = harness();
const create = s.passwordPamFactory.createObject;
s.passwordPamFactory.createObject = (...args) => Object.assign(create(...args), { start: () => false });
s.submitPassword('');
assert.equal(s.unlocks, 0);
assert.equal(s.failedAttempts, 1);

// PIN change: fresh answers per prompt; success never unlocks; notices clear on reset.
s = harness();
s.beginPinRecovery();
recovery = s.pinRecovery;
recovery.line({ type: 'prompt', visible: false, text: 'New PIN:' });
s.submitPassword('dummy-new');
s.submitPassword('no replay');
assert.equal(recovery.writes.length, 1);
recovery.line({ type: 'prompt', visible: false, text: 'Confirm PIN:' });
s.submitPassword('');
assert.equal(recovery.writes.length, 1);
s.submitPassword('dummy-new');
recovery.line({ type: 'info', text: 'Authenticator number 42' });
assert.ok(s.passwordNotices.includes('Authenticator number 42'));
recovery.line({ type: 'prompt', visible: true, text: 'One-time code:' });
assert.equal(s.passwordResponseVisible, true);
s.submitPassword('dummy-otp');
recovery.line({ type: 'result', success: true });
assert.equal(s.unlocks, 0);
recovery.exit(0);
assert.equal(s.lastRecoveryResult, 'success');
assert.equal(s.lockRequested, true);
assert.equal(s.unlocks, 0);
assert.ok(s.passwordNotices[0].includes('PIN updated'));
s.resetAuthenticationState();
assert.equal(s.passwordNotices.length, 0);
assert.equal(s.passwordMessageIsError, false);
s.submitPassword('');
c = s.passwordPam;
c.prompt('PIN:');
s.submitPassword('dummy-new');
c.complete(0);
assert.equal(s.unlocks, 1);

// Cancel closes the helper's stdin, then SIGTERM, then SIGKILL; the process is
// kept until it exits, so no other attempt can overlap it.
s = harness();
s.beginPinRecovery();
recovery = s.pinRecovery;
recovery.line({ type: 'prompt', visible: false, text: 'New PIN:' });
s.enteredPassword = 'erase';
s.cancelPasswordAttempt();
assert.equal(s.enteredPassword, '');
assert.equal(s.lastRecoveryResult, 'cancelled');
assert.equal(recovery.stdinEnabled, false);
assert.deepEqual(recovery.signals, []);
assert.equal(s.pinRecoveryStopTimer.running, true);
s.escalatePinRecoveryStop();
s.cancelPasswordAttempt(); // A second Esc must not restart the escalation.
s.escalatePinRecoveryStop();
assert.deepEqual(recovery.signals, [15, 9]);
s.submitPassword('');
s.beginPinRecovery();
assert.equal(s.passwordPam, null);
assert.equal(s.pinRecovery, recovery);
recovery.line({ type: 'result', success: true });
recovery.exit(0);
assert.equal(s.lastRecoveryResult, 'cancelled');
assert.equal(s.unlocks, 0);
assert.equal(s.pinRecoveryStopTimer.running, false);
s.escalatePinRecoveryStop();
assert.deepEqual(recovery.signals, [15, 9]);
s.submitPassword('');
c = s.passwordPam;
recovery.exit(0);
assert.equal(s.passwordPam, c);
assert.equal(s.unlocks, 0);

// The helper sends at most 256 messages; its result line is not one of them.
s = harness();
s.beginPinRecovery();
recovery = s.pinRecovery;
for (let i = 0; i < 256; i++) recovery.line({ type: 'info', text: `step ${i}` });
assert.equal(s.passwordNotices.length, 8);
recovery.line({ type: 'result', success: true });
recovery.exit(0);
assert.equal(s.lastRecoveryResult, 'success');
s = harness();
s.beginPinRecovery();
recovery = s.pinRecovery;
for (let i = 0; i < 257; i++) recovery.line({ type: 'info', text: 'x' });
assert.equal(s.lastRecoveryResult, 'failed');
assert.equal(recovery.stdinEnabled, false);

// Failed or cancelled PIN changes back off 30 s, doubling to 15 min.
s = harness();
const waits = [];
for (let i = 0; i < 7; i++) {
  s.beginPinRecovery();
  recovery = s.pinRecovery;
  if (i % 2) s.cancelPasswordAttempt();
  recovery.exit(1);
  waits.push(s.recoveryBackoffMs);
  s.now += s.recoveryBackoffMs - 1000;
  s.beginPinRecovery();
  assert.equal(s.pinRecovery, null);
  assert.match(s.failureMessage, /again in 1 s/);
  s.now += 1000;
}
assert.deepEqual(waits, [30000, 60000, 120000, 240000, 480000, 900000, 900000]);
s.now -= 900000;
s.beginPinRecovery();
assert.match(s.failureMessage, /again in 15 min/);
// Success or an unlock clears it.
s.now += 900000;
s.beginPinRecovery();
recovery = s.pinRecovery;
recovery.line({ type: 'result', success: true });
recovery.exit(0);
assert.equal(s.recoveryBackoffMs, 0);
s.beginPinRecovery();
s.pinRecovery.exit(1);
s.finishUnlock();
assert.equal(s.recoveryRetryAtMs, 0);
s.lockRequested = true;
s.beginPinRecovery();
assert.notEqual(s.pinRecovery, null);

// Invalid helper output fails closed.
s = harness();
s.beginPinRecovery();
recovery = s.pinRecovery;
s.handlePinRecoveryLine(recovery, recovery.attemptId, 'bad-json');
assert.equal(recovery.stdinEnabled, false);
assert.equal(s.lastRecoveryResult, 'failed');
recovery.exit(1);
assert.equal(s.unlocks, 0);

// A missing PIN helper is a bounded error; normal sign-in still works.
s = harness();
s.pinRecoveryAvailable = false;
s.beginPinRecovery();
assert.equal(s.pinRecovery, null);
assert.equal(s.unlocks, 0);
assert.equal(s.lockRequested, true);
assert.equal(s.lastRecoveryResult, 'failed');
assert.match(s.failureMessage, /helper unavailable/);
s.submitPassword('');
c = s.passwordPam;
c.prompt('PIN:');
s.submitPassword('dummy');
c.complete(0);
assert.equal(s.unlocks, 1);

// Activity during a conversation restarts its one-minute cancel timer.
const wake = loadFunctions(service, ['runWake'], {
  wakeProcess: { running: true },
  lockRequested: true,
  authenticatingPassword: true,
  conversationIdleTimer: stoppedTimer(),
  armBlankTimer() {},
  resumeFingerprint() {},
  nudgeFingerprint() {},
});
wake.runWake();
assert.equal(wake.conversationIdleTimer.running, true);
wake.conversationIdleTimer.stop();
wake.authenticatingPassword = false;
wake.runWake();
assert.equal(wake.conversationIdleTimer.running, false);

// Only a missing PAM file refuses a lock, and it says so; the setup check
// runs after locking and never blocks it.
function lockHarness(pamConfigured) {
  const state = {
    passwordPamConfigured: pamConfigured,
    lockRequested: false,
    lockWarning: 'stale',
    events: [],
    resets: 0,
    lockRefusedNotice: { running: false },
    lockPreflight: { running: false },
    lockPreflightTimeout: stoppedTimer(),
    pinHelperCheck: { running: false },
    Qt: { callLater() {} },
    logEvent(event) { state.events.push(event); },
    resetAuthenticationState() { state.resets++; },
    armBlankTimer() {},
    queueSessionLock() {},
    refreshBackground() {},
    refreshFingerprintStatus() {},
  };
  state.root = state;
  return loadFunctions(service, ['refuseLock', 'checkLockSetup', 'beginLock'], state);
}
let lock = lockHarness(false);
assert.equal(lock.beginLock(), false);
assert.equal(lock.lockRequested, false);
assert.equal(lock.resets, 0);
assert.equal(lock.lockRefusedNotice.running, true);
assert.deepEqual(lock.events, ['lock-denied: missing-pam']);
assert.equal(lock.lockPreflight.running, false);
lock = lockHarness(true);
assert.equal(lock.beginLock(), true);
assert.equal(lock.lockRequested, true);
assert.equal(lock.resets, 1);
assert.equal(lock.lockPreflight.running, true);
assert.equal(lock.lockPreflightTimeout.running, true);
assert.equal(lock.pinHelperCheck.running, true);
assert.equal(lock.lockWarning, '');
assert.equal(lock.lockRefusedNotice.running, false);

// Contracts with the companion and with the PAM text rendering.
assert.match(service, /path: "\/etc\/pam.d\/omarchy-himmelblau-lock-password"/);
assert.match(service, /command: \["\/usr\/bin\/omarchy-himmelblau", "locker-preflight"\]/);
assert.ok(view.includes('textFormat: Text.PlainText'));
assert.ok(view.includes('root.responseVisible ? TextInput.Normal : TextInput.Password'));

console.log('PASS: lock PAM conversations, PIN change, cancel/stop, backoff, lock refusal and setup check');
