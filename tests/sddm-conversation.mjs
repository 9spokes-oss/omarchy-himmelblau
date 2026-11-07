import assert from 'node:assert/strict';
import { loadFunctions, patchedFixture } from './qml-functions.mjs';

const source = patchedFixture('Main.qml', 'sddm-conversation.patch');

// The SDDM theme with a fake greeter proxy that records calls.
function fixture() {
  const calls = { starts: [], responses: [], cancels: [] };
  const state = {
    conversationAvailable: true,
    currentUser: 'alice@example.test',
    sessionIndex: 0,
    activeAttempt: 0,
    activePrompt: 0,
    lastPrompt: 0,
    lastSentPrompt: 0,
    responseHidden: true,
    recovering: false,
    cancelling: false,
    authorized: false,
    sessionStarted: false,
    noticeError: false,
    promptText: '',
    notices: [],
    loginFailed: false,
    password: { text: '', forceActiveFocus() {} },
    Qt: { Key_Escape: 1, Key_R: 2, ControlModifier: 4 },
    sddm: {
      beginConversation(user, session, recovery) {
        calls.starts.push({ user, session, recovery });
        return calls.starts.length;
      },
      respondConversation(attempt, prompt, response) { calls.responses.push({ attempt, prompt, response }); },
      cancelConversation(attempt) { calls.cancels.push(attempt); },
    },
  };
  state.root = state;
  loadFunctions(source, ['startConversation', 'submitResponse', 'cancelConversation', 'keyAction',
    'onConversationPrompt', 'onConversationMessage', 'onConversationAuthorized', 'onConversationFinished',
    'onSocketDisconnected'], state);
  return { state, calls };
}

let s, calls;

// No conversation API, no conversation.
({ state: s, calls } = fixture());
s.conversationAvailable = false;
s.startConversation(false);
s.startConversation(true);
assert.equal(calls.starts.length, 0);

// Prompts are correlated by attempt and prompt id, and each gets one answer.
({ state: s, calls } = fixture());
s.password.text = 'discarded-only-before-start';
s.startConversation(false);
assert.equal(s.password.text, '');
assert.deepEqual(calls.starts, [{ user: 'alice@example.test', session: 0, recovery: false }]);
s.onConversationPrompt(99, 1, 'wrong attempt', true);
assert.equal(s.activePrompt, 0);
s.onConversationPrompt(1, 1, 'Password:', true);
s.password.text = 'dummy-password';
s.submitResponse();
s.submitResponse();
assert.equal(calls.responses.length, 1);
assert.equal(s.password.text, '');
s.onConversationPrompt(1, 1, 'duplicate prompt', true);
assert.equal(s.activePrompt, 0);
s.onConversationMessage(1, 'Use number 42 in Authenticator', false);
s.onConversationPrompt(1, 2, 'OTP:', false);
assert.equal(s.responseHidden, false);
assert.ok(s.notices.includes('Use number 42 in Authenticator'));
s.password.text = 'dummy-otp';
s.submitResponse();
assert.equal(calls.responses.length, 2);
assert.equal(calls.responses[1].prompt, 2);

// Cancel drops late prompts and blocks a new start until the daemon finishes.
s.cancelConversation();
assert.equal(s.cancelling, true);
assert.equal(s.activePrompt, 0);
s.onConversationPrompt(1, 3, 'late prompt', true);
assert.equal(s.activePrompt, 0);
s.startConversation(true);
assert.equal(calls.starts.length, 1);
s.onConversationFinished(1, 1);
assert.equal(s.activeAttempt, 0);

// PIN recovery ignores stale finish events and never starts a session.
s.startConversation(true);
assert.equal(s.activeAttempt, 2);
assert.equal(calls.starts[1].recovery, true);
s.onConversationFinished(1, 3);
assert.equal(s.activeAttempt, 2);
for (const [id, text] of [[1, 'New PIN:'], [2, 'Confirm PIN:']]) {
  s.onConversationPrompt(2, id, text, true);
  assert.equal(s.password.text, '');
  s.password.text = 'dummy-new';
  s.submitResponse();
}
s.onConversationFinished(2, 3);
assert.equal(s.sessionStarted, false);
assert.equal(s.activeAttempt, 0);
assert.ok(s.notices[0].includes('PIN updated'));
s.startConversation(false);
assert.equal(s.activeAttempt, 3);
s.onConversationFinished(3, 2);
assert.equal(s.sessionStarted, true);
s.startConversation(true);
assert.equal(calls.starts.length, 3);

// The authorization cutoff cannot be falsely advertised as a cancelled login.
({ state: s, calls } = fixture());
s.startConversation(false);
s.onConversationPrompt(1, 1, 'Session prompt:', true);
s.cancelConversation();
s.onConversationAuthorized(1);
assert.equal(s.authorized, true);
assert.equal(s.cancelling, false);
s.onConversationPrompt(1, 1, 'Session prompt:', true);
assert.equal(s.activePrompt, 1);
s.password.text = 'fresh-session-response';
s.submitResponse();
s.cancelConversation();
assert.equal(calls.cancels.length, 1);

// A login success code in recovery mode never becomes a session-start signal.
({ state: s, calls } = fixture());
s.startConversation(true);
s.onConversationFinished(1, 2);
assert.equal(s.sessionStarted, false);
s.onSocketDisconnected();
assert.equal(s.password.text, '');
assert.equal(s.activeAttempt, 0);

// Ordinary typing is never swallowed.
assert.equal(s.keyAction({ key: 999, modifiers: 0, accepted: true }), false);

assert.ok(source.includes('enabled: root.conversationAvailable && root.activeAttempt === 0 && !root.sessionStarted'));
assert.ok(source.includes('textFormat: Text.PlainText'));
assert.ok(!source.includes('console.log'));

console.log('PASS: SDDM capability guard, fresh correlated prompts, cancel/authorization cutoff, stale events, PIN recovery never launches a session');
