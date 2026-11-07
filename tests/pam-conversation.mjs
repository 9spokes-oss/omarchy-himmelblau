import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import vm from 'node:vm';
const root = new URL('../', import.meta.url);
const original = readFileSync(new URL('tests/fixtures/Service.qml', root), 'utf8');
function patched(filename, asset) {
  const work = mkdtempSync(join(tmpdir(), 'omarchy-pam-conversation-'));
  try {
    writeFileSync(join(work, filename), readFileSync(new URL(`tests/fixtures/${filename}`, root)));
    execFileSync('patch', ['--batch', '--fuzz=0', '-p1', '-d', work], {timeout:5000,input:readFileSync(new URL(`assets/${asset}`, root))});
    return readFileSync(join(work, filename), 'utf8');
  } finally { rmSync(work, {recursive:true}); }
}
const service = patched('Service.qml', 'lock-conversation.patch');
const view = patched('LockView.qml', 'lock-view.patch');
function fn(source, name) {
 const marker=`function ${name}(`, start=source.indexOf(marker); assert.ok(start>=0,name);
 let end=source.indexOf('{', start)+1, depth=1;
 while(depth && end<source.length) { if(source[end]==='{')depth++; if(source[end]==='}')depth--;end++; }
 assert.equal(depth,0);return source.slice(start,end);
}
function harness() {
 const state={pinRecoveryAvailable:true,lockRequested:true, authenticatingPassword:false,passwordPam:null,passwordAttempt:0,
  awaitingPasswordResponse:false,passwordResponseVisible:false,passwordPrompt:'',passwordNotices:[],
  passwordMessageIsError:false,pinRecovery:null,recoveringPIN:false,recoveryAttempt:0,lastRecoveryResult:'none',pinRecoverySignal:15,enteredPassword:'',failureMessage:'',failedAttempts:0,unlocks:0,contexts:[],
  runWake(){},logEvent(event){if(event==='unlocked')state.unlocks++;},PamResult:{Success:0},
  I18n:{t(){return{arg(n){return `failed ${n}`;}}},formatNumber(n){return n;}}};
 state.sessionLock={locked:true,secure:true};state.fingerprintPam={active:false};
 for(const name of ['sessionLockStabilizeTimer','pendingSessionLockTimer','idleBlankTimer','fingerprintRecheckTimer','fingerprintRetryTimer','fingerprintReachTimer','fingerprintResumeTimer'])state[name]={stop(){}};
 state.pinRecoveryStopTimer={running:false,restart(){this.running=true;},stop(){this.running=false;}};
 state.pinRecoveryFactory={createObject(parent,properties){return {...properties,running:false,stdinEnabled:true,reportedSuccess:false,messageCount:0,writes:[],signals:[],
  write(text){this.writes.push(text);},signal(number){this.signals.push(number);},destroy(){this.destroyed=true;},
  line(message){state.handlePinRecoveryLine(this,this.attemptId,JSON.stringify(message));},
  exit(code){this.running=false;state.finishPinRecovery(this,this.attemptId,code);}};}};
 state.root=state;
 state.passwordPamFactory={createObject(parent, properties){
  const context={...properties,active:false,responseRequired:false,responseVisible:false,message:'',messageIsError:false,replies:[],
   start(){this.active=true;return true;},respond(response){this.replies.push(response);},
   abort(){this.active=false;state.completePasswordAttempt(this,this.attemptId,0);state.handlePasswordFailure(this,this.attemptId);},
   destroy(){this.destroyed=true;},
   prompt(message,visible=false){this.message=message;this.responseRequired=true;this.responseVisible=visible;this.messageIsError=false;state.handlePasswordMessage(this,this.attemptId);},
   info(message,error=false){this.message=message;this.responseRequired=false;this.messageIsError=error;state.handlePasswordMessage(this,this.attemptId);},
   complete(result){this.active=false;state.completePasswordAttempt(this,this.attemptId,result);}};
  state.contexts.push(context);return context;
 }};
 const scope=vm.createContext(state);
 for(const name of ['currentPasswordAttempt','clearPasswordPrompt','submitPassword','handlePasswordMessage','settlePasswordAttempt','handlePasswordFailure','cancelPasswordAttempt','completePasswordAttempt','beginPinRecovery','currentPinRecovery','handlePinRecoveryLine','stopPinRecovery','escalatePinRecoveryStop','finishPinRecovery','currentlyLocked','resetAuthenticationState','finishUnlock'])vm.runInContext(fn(service,name),scope,{timeout:1000});
 return state;
}
let s=harness();s.submitPassword('must-not-silently-discard');assert.equal(s.contexts.length,0);
s.submitPassword('');let c=s.passwordPam;assert.equal(c.replies.length,0);
c.prompt('PIN:');assert.equal(s.awaitingPasswordResponse,true);assert.equal(s.passwordResponseVisible,false);
s.submitPassword('');assert.equal(c.replies.length,0);assert.equal(s.awaitingPasswordResponse,true); // stray Enter is not a PAM failure
s.enteredPassword='dummy-pin';s.submitPassword(s.enteredPassword);assert.deepEqual(c.replies,['dummy-pin']);assert.equal(s.enteredPassword,'');
s.submitPassword('must-not-replay');assert.equal(c.replies.length,1); // responseRequired remains true in Quickshell.
c.complete(0);assert.equal(s.unlocks,1);

s=harness();s.submitPassword('');c=s.passwordPam;
c.prompt('Password:');s.submitPassword('dummy-password');
c.info('Enter number 42 in Microsoft Authenticator');assert.ok(s.passwordNotices.includes('Enter number 42 in Microsoft Authenticator'));
for(let i=0;i<20;i++)c.info('Waiting for approval');assert.equal(c.replies.length,1);assert.equal(s.awaitingPasswordResponse,false);
c.prompt('Continue?',true);s.submitPassword('');
c.prompt('One-time code:',true);assert.equal(s.passwordResponseVisible,true);s.submitPassword('dummy-otp');
assert.deepEqual(c.replies,['dummy-password','','dummy-otp']);assert.equal(s.unlocks,0);c.complete(0);assert.equal(s.unlocks,1);

s=harness();s.submitPassword('');c=s.passwordPam;
for(const prompt of ['New PIN:','Confirm PIN:']){c.prompt(prompt);assert.equal(s.enteredPassword,'');assert.equal(s.passwordResponseVisible,false);s.submitPassword('dummy-new-pin');}
assert.deepEqual(c.replies,['dummy-new-pin','dummy-new-pin']);c.info('PIN enrollment failed',true);assert.equal(s.passwordMessageIsError,true);c.complete(1);assert.equal(s.unlocks,0);assert.equal(s.failedAttempts,1);
c.complete(0);assert.equal(s.unlocks,0);s.handlePasswordFailure(c,c.attemptId);assert.equal(s.failedAttempts,1);
s.submitPassword('');let next=s.passwordPam;assert.notEqual(next,c);c.prompt('stale');c.complete(0);assert.equal(s.passwordPam,next);assert.equal(s.unlocks,0);
next.prompt('PIN:');s.cancelPasswordAttempt();assert.equal(s.passwordPam,null);assert.equal(s.enteredPassword,'');assert.equal(s.passwordNotices.length,0);assert.equal(s.unlocks,0);assert.equal(s.failedAttempts,1);
s.submitPassword('');const last=s.passwordPam;next.complete(0);assert.equal(s.passwordPam,last);assert.equal(s.unlocks,0);last.prompt('PIN:');s.submitPassword('dummy-pin');last.complete(0);assert.equal(s.unlocks,1);

// Real fingerprint-success path: finishUnlock calls actual reset/cancellation before abort callbacks.
s=harness();s.submitPassword('');c=s.passwordPam;c.prompt('PIN:');s.finishUnlock();
assert.equal(s.unlocks,1);assert.equal(c.destroyed,true);assert.equal(s.passwordPam,null);assert.equal(s.enteredPassword,'');
c.complete(0);c.prompt('late');assert.equal(s.unlocks,1);assert.equal(s.failedAttempts,0);
s.lockRequested=true;s.sessionLock.locked=true;s.submitPassword('');next=s.passwordPam;c.complete(0);assert.equal(s.passwordPam,next);assert.equal(s.unlocks,1);
// Allocation/start failure settles without unlock, and a later explicit retry still works.
s=harness();s.passwordPamFactory.createObject=()=>null;s.submitPassword('');assert.equal(s.unlocks,0);assert.equal(s.failedAttempts,1);assert.equal(s.authenticatingPassword,false);
s=harness();const create=s.passwordPamFactory.createObject;s.passwordPamFactory.createObject=(...args)=>{let ctx=create(...args);ctx.start=()=>false;return ctx;};s.submitPassword('');assert.equal(s.unlocks,0);assert.equal(s.failedAttempts,1);
// Password-only PIN replacement: confirmation/MFA fresh responses, result alone never unlocks.
s=harness();s.beginPinRecovery();let recovery=s.pinRecovery;
recovery.line({type:'prompt',visible:false,text:'New PIN:'});s.submitPassword('dummy-new');s.submitPassword('no replay');assert.equal(recovery.writes.length,1);
recovery.line({type:'prompt',visible:false,text:'Confirm PIN:'});s.submitPassword('');assert.equal(recovery.writes.length,1);s.submitPassword('dummy-new');
recovery.line({type:'info',text:'Authenticator number 42'});assert.ok(s.passwordNotices.includes('Authenticator number 42'));
recovery.line({type:'prompt',visible:true,text:'One-time code:'});assert.equal(s.passwordResponseVisible,true);s.submitPassword('dummy-otp');
recovery.line({type:'result',success:true});assert.equal(s.unlocks,0);recovery.exit(0);
assert.equal(s.lastRecoveryResult,'success');assert.equal(s.lockRequested,true);assert.equal(s.unlocks,0);assert.ok(s.passwordNotices[0].includes('PIN updated'));
s.resetAuthenticationState();assert.equal(s.passwordNotices.length,0);assert.equal(s.passwordMessageIsError,false);
s.submitPassword('');c=s.passwordPam;c.prompt('PIN:');s.submitPassword('dummy-new');c.complete(0);assert.equal(s.unlocks,1);
// Cancellation keeps process ownership until actual exit, blocking overlapping auth/reset.
s=harness();s.beginPinRecovery();recovery=s.pinRecovery;recovery.line({type:'prompt',visible:false,text:'New PIN:'});s.enteredPassword='erase';s.cancelPasswordAttempt();
assert.equal(s.enteredPassword,'');assert.equal(s.lastRecoveryResult,'cancelled');
// Stop closes stdin first; SIGTERM then SIGKILL only while the helper keeps running.
assert.equal(recovery.stdinEnabled,false);assert.deepEqual(recovery.signals,[]);assert.equal(s.pinRecoveryStopTimer.running,true);
s.escalatePinRecoveryStop();s.cancelPasswordAttempt();s.escalatePinRecoveryStop();assert.deepEqual(recovery.signals,[15,9]);
s.submitPassword('');s.beginPinRecovery();assert.equal(s.passwordPam,null);assert.equal(s.pinRecovery,recovery);
recovery.line({type:'result',success:true});recovery.exit(0);assert.equal(s.lastRecoveryResult,'cancelled');assert.equal(s.unlocks,0);assert.equal(s.pinRecoveryStopTimer.running,false);
s.escalatePinRecoveryStop();assert.deepEqual(recovery.signals,[15,9]);
s.submitPassword('');c=s.passwordPam;recovery.exit(0);assert.equal(s.passwordPam,c);assert.equal(s.unlocks,0);
// Invalid helper protocol fails closed; old callbacks cannot modify a new operation.
s=harness();s.beginPinRecovery();recovery=s.pinRecovery;s.handlePinRecoveryLine(recovery,recovery.attemptId,'bad-json');assert.equal(recovery.stdinEnabled,false);assert.equal(s.lastRecoveryResult,'failed');recovery.exit(1);assert.equal(s.unlocks,0);

assert.ok(view.includes('textFormat: Text.PlainText'));
assert.ok(view.includes('root.responseVisible ? TextInput.Normal : TextInput.Password'));
assert.ok(view.includes('root.cancelRequested()'));
assert.ok(service.includes('context === passwordPam && attempt === passwordAttempt'));
assert.ok(!service.includes('passwordPam.respond(pendingPassword)'));
assert.ok(!service.includes('onResponseRequiredChanged: root.'));

// Missing recovery helper is a bounded error; normal auth and secure lock survive.
s=harness();s.pinRecoveryAvailable=false;s.beginPinRecovery();
assert.equal(s.pinRecovery,null);assert.equal(s.unlocks,0);assert.equal(s.lockRequested,true);
assert.equal(s.lastRecoveryResult,'failed');assert.match(s.failureMessage,/helper unavailable/);
s.submitPassword('');c=s.passwordPam;c.prompt('PIN:');s.submitPassword('dummy');c.complete(0);assert.equal(s.unlocks,1);

// User activity during a conversation restarts its one-minute cancel timer.
const wake={wakeProcess:{running:true},lockRequested:true,authenticatingPassword:true,restarts:0,
 armBlankTimer(){},resumeFingerprint(){},nudgeFingerprint(){},conversationIdleTimer:{restart(){wake.restarts++;}}};
vm.runInContext(fn(service,'runWake'),vm.createContext(wake),{timeout:1000});
wake.runWake();assert.equal(wake.restarts,1);wake.authenticatingPassword=false;wake.runWake();assert.equal(wake.restarts,1);

// Only a missing PAM file refuses a lock, visibly; the setup check runs after locking.
function guardHarness(pam) {
 const state={passwordPamConfigured:pam,lockRequested:false,lockWarning:'stale',events:[],started:0,
  logEvent(event){state.events.push(event);},resetAuthenticationState(){state.started++;},armBlankTimer(){},queueSessionLock(){},
  refreshBackground(){},refreshFingerprintStatus(){},Qt:{callLater(){}},
  lockRefusedNotice:{running:false},lockPreflight:{running:false},lockPreflightTimeout:{restart(){}},pinHelperCheck:{running:false}};
 state.root=state;
 const scope=vm.createContext(state);
 for(const name of ['refuseLock','checkLockSetup','beginLock'])vm.runInContext(fn(service,name),scope,{timeout:1000});
 return state;
}
let guard=guardHarness(false);assert.equal(guard.beginLock(),false);assert.equal(guard.lockRequested,false);assert.equal(guard.started,0);
assert.equal(guard.lockRefusedNotice.running,true);assert.deepEqual(guard.events,['lock-denied: missing-pam']);assert.equal(guard.lockPreflight.running,false);
guard=guardHarness(true);assert.equal(guard.beginLock(),true);assert.equal(guard.lockRequested,true);assert.equal(guard.started,1);
assert.equal(guard.lockPreflight.running,true);assert.equal(guard.pinHelperCheck.running,true);assert.equal(guard.lockWarning,'');assert.equal(guard.lockRefusedNotice.running,false);
assert.match(service,/path: "\/etc\/pam.d\/omarchy-himmelblau-lock-password"/);
assert.match(service,/command: \["\/usr\/bin\/omarchy-himmelblau", "locker-preflight"\]/);
console.log('PASS: auth and PIN recovery conversations, info/poll/OTP/confirmation, real fingerprint reset, failure/cancel/retry/late callbacks, no replay or reset unlock');
