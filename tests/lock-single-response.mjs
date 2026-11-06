#!/usr/bin/env node
// Exercise JavaScript extracted from captured QML, with synthetic PAM only.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import vm from 'node:vm';

const rootDir = new URL('../', import.meta.url);
const original = readFileSync(new URL('tests/fixtures/Service.qml', rootDir), 'utf8');
const patch = readFileSync(new URL('assets/lock-single-response.patch', rootDir), 'utf8');
const work = mkdtempSync(join(tmpdir(), 'omarchy-lock-source-'));
let patched;
try {
  writeFileSync(join(work, 'Service.qml'), original);
  execFileSync('patch', ['--fuzz=0', '-p1', '-d', work], { input: patch });
  patched = readFileSync(join(work, 'Service.qml'), 'utf8');
} finally {
  rmSync(work, { recursive: true });
}

function extractFunction(source, marker) {
  const start = source.indexOf(marker);
  assert.ok(start >= 0, marker);
  const brace = source.indexOf('{', start);
  let depth = 1, end = brace + 1;
  // These narrow source blocks have balanced braces, including their strings/comments.
  while (depth && end < source.length) {
    if (source[end] === '{') depth++;
    if (source[end] === '}') depth--;
    end++;
  }
  assert.equal(depth, 0);
  return source.slice(start, end);
}

function harness(source) {
  const later = [];
  const state = {
    lockRequested: true, authenticatingPassword: false, pendingPassword: '',
    enteredPassword: '', passwordResponseSent: false, failureMessage: '', failedAttempts: 0,
    unlocks: 0, replies: [], aborts: 0,
    Qt: { callLater(fn) { later.push(fn); } },
    PamResult: { Success: 0 },
    I18n: { t() { return { arg(n) { return `failure ${n}`; } }; }, formatNumber(n) { return n; } },
    runWake() {},
    finishUnlock() { state.unlocks++; state.lockRequested = false; },
  };
  state.root = state;
  state.passwordPam = {
    active: false, responseRequired: false,
    start() { this.active = true; this.responseRequired = false; return true; },
    // Quickshell 0.3.2 leaves this flag set until another PAM message/abort.
    respond(value) { state.replies.push(value); },
    abort() {
      state.aborts++;
      this.active = false;
      this.responseRequired = false;
      // Adversarial synchronous events: neither may settle/unlock a second time.
      state.handlePasswordFailure();
      state.complete(0);
    },
  };
  const context = vm.createContext(state);
  for (const name of ['submitPassword', 'respondToPasswordPrompt', 'handlePasswordFailure']) {
    vm.runInContext(extractFunction(source, `function ${name}(`), context);
  }
  const passwordContext = source.slice(source.indexOf('    id: passwordPam'));
  const completion = extractFunction(passwordContext, 'onCompleted: function(result)');
  vm.runInContext(`var complete = ${completion.slice('onCompleted: '.length)}`, context);
  return {
    state,
    flush() { while (later.length) later.shift()(); },
    prompt() { state.passwordPam.responseRequired = true; state.respondToPasswordPrompt(); },
    complete(result) { state.passwordPam.active = false; state.passwordPam.responseRequired = false; state.complete(result); },
  };
}

assert.ok(!patched.includes('onResponseRequiredChanged: root.respondToPasswordPrompt()'));
assert.ok(!patched.includes('Qt.callLater(respondToPasswordPrompt)'));

// Reproduce the source defect: one submission is sent again on the next prompt.
let h = harness(original);
h.state.submitPassword('dummy-old'); h.flush(); h.prompt(); h.prompt();
assert.deepEqual(h.state.replies, ['dummy-old', 'dummy-old']);

// Duplicate helper callbacks when responseRequired is false are harmless.
h = harness(patched);
h.state.submitPassword('dummy-first'); h.prompt(); h.flush();
h.state.passwordPam.responseRequired = false;
h.state.respondToPasswordPrompt(); h.state.respondToPasswordPrompt();
assert.deepEqual(h.state.replies, ['dummy-first']);
assert.equal(h.state.aborts, 0);
assert.equal(h.state.pendingPassword, '');

// A genuine further prompt aborts; abort callbacks cannot double count or unlock.
h.prompt();
assert.deepEqual(h.state.replies, ['dummy-first']);
assert.equal(h.state.aborts, 1);
assert.equal(h.state.failedAttempts, 1);
assert.equal(h.state.unlocks, 0);
assert.equal(h.state.authenticatingPassword, false);
assert.equal(h.state.pendingPassword, '');
assert.equal(h.state.enteredPassword, '');
h.complete(0); h.state.handlePasswordFailure();
assert.equal(h.state.unlocks, 0); assert.equal(h.state.failedAttempts, 1);

// Only a new explicit submission resets the guard and may succeed.
h.state.submitPassword('dummy-second'); h.prompt(); h.flush();
assert.deepEqual(h.state.replies, ['dummy-first', 'dummy-second']);
h.complete(0);
assert.equal(h.state.unlocks, 1); assert.equal(h.state.failedAttempts, 1);
assert.equal(h.state.pendingPassword, '');

// A failure followed by completion/error counts only once.
h = harness(patched); h.state.submitPassword('dummy-failure'); h.prompt(); h.flush();
h.complete(1); h.state.handlePasswordFailure(); h.complete(0);
assert.equal(h.state.failedAttempts, 1); assert.equal(h.state.unlocks, 0);

// Reentrant repeated prompt from respond itself sees the guard already set.
h = harness(patched);
const respond = h.state.passwordPam.respond;
h.state.passwordPam.respond = function(value) {
  respond.call(this, value);
  h.prompt();
};
h.state.submitPassword('dummy-reentrant'); h.prompt(); h.flush();
assert.deepEqual(h.state.replies, ['dummy-reentrant']);
assert.equal(h.state.aborts, 1); assert.equal(h.state.failedAttempts, 1);
assert.equal(h.state.unlocks, 0);
console.log('PASS: original replay reproduced; patched single reply, abort, explicit retry, duplicate callbacks and reentrancy');
