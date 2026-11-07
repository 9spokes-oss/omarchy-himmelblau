import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';import {join} from 'node:path';import vm from 'node:vm';
const root=new URL('../',import.meta.url),work=mkdtempSync(join(tmpdir(),'sddm-theme-proof-'));
let source;
try{writeFileSync(join(work,'Main.qml'),readFileSync(new URL('tests/fixtures/Main.qml',root)));execFileSync('patch',['--batch','--fuzz=0','-p1','-d',work],{timeout:5000,input:readFileSync(new URL('assets/sddm-conversation.patch',root))});source=readFileSync(join(work,'Main.qml'),'utf8');}finally{rmSync(work,{recursive:true});}
function fn(name){const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,name);let end=source.indexOf('{',start)+1,depth=1;while(depth && end<source.length){if(source[end]==='{')depth++;if(source[end]==='}')depth--;end++;}assert.equal(depth,0,'unterminated function');return source.slice(start,end);}
function fixture(){
 const calls={starts:[],responses:[],cancels:[]};const state={conversationAvailable:true,currentUser:'alice@example.test',sessionIndex:0,activeAttempt:0,activePrompt:0,lastPrompt:0,lastSentPrompt:0,responseHidden:true,recovering:false,cancelling:false,authorized:false,sessionStarted:false,noticeError:false,promptText:'',notices:[],loginFailed:false,
 password:{text:'',forceActiveFocus(){}},Qt:{Key_Escape:1,Key_R:2,ControlModifier:4}};state.root=state;
 state.sddm={beginConversation(user,session,recovery){calls.starts.push({user,session,recovery});return calls.starts.length;},respondConversation(attempt,prompt,response){calls.responses.push({attempt,prompt,response});},cancelConversation(attempt){calls.cancels.push(attempt);}};
 const scope=vm.createContext(state);for(const name of ['startConversation','submitResponse','cancelConversation','keyAction','onConversationPrompt','onConversationMessage','onConversationAuthorized','onConversationFinished','onSocketDisconnected'])vm.runInContext(fn(name),scope,{timeout:1000});
 return{state,calls};
}
let {state:s,calls}=fixture();s.conversationAvailable=false;s.startConversation(false);s.startConversation(true);assert.equal(calls.starts.length,0);
({state:s,calls}=fixture());s.password.text='discarded-only-before-start';s.startConversation(false);assert.equal(s.password.text,'');assert.deepEqual(calls.starts,[{user:'alice@example.test',session:0,recovery:false}]);
s.onConversationPrompt(99,1,'wrong attempt',true);assert.equal(s.activePrompt,0);s.onConversationPrompt(1,1,'Password:',true);s.password.text='dummy-password';s.submitResponse();s.submitResponse();assert.equal(calls.responses.length,1);assert.equal(s.password.text,'');
s.onConversationPrompt(1,1,'duplicate prompt',true);assert.equal(s.activePrompt,0);s.onConversationMessage(1,'Use number 42 in Authenticator',false);s.onConversationPrompt(1,2,'OTP:',false);assert.equal(s.responseHidden,false);assert.ok(s.notices.includes('Use number 42 in Authenticator'));
s.password.text='dummy-otp';s.submitResponse();assert.equal(calls.responses.length,2);assert.equal(calls.responses[1].prompt,2);
s.cancelConversation();assert.equal(s.cancelling,true);assert.equal(s.activePrompt,0);s.onConversationPrompt(1,3,'late prompt',true);assert.equal(s.activePrompt,0);s.startConversation(true);assert.equal(calls.starts.length,1);
s.onConversationFinished(1,1);assert.equal(s.activeAttempt,0);s.startConversation(true);assert.equal(s.activeAttempt,2);assert.equal(calls.starts[1].recovery,true);s.onConversationFinished(1,3);assert.equal(s.activeAttempt,2);
for(const [id,text] of [[1,'New PIN:'],[2,'Confirm PIN:']]){s.onConversationPrompt(2,id,text,true);assert.equal(s.password.text,'');s.password.text='dummy-new';s.submitResponse();}
s.onConversationFinished(2,3);assert.equal(s.sessionStarted,false);assert.equal(s.activeAttempt,0);assert.ok(s.notices[0].includes('PIN updated'));s.startConversation(false);assert.equal(s.activeAttempt,3);s.onConversationFinished(3,2);assert.equal(s.sessionStarted,true);s.startConversation(true);assert.equal(calls.starts.length,3);
// The authorization cutoff cannot be falsely advertised as a cancelled login.
({state:s,calls}=fixture());s.startConversation(false);s.onConversationPrompt(1,1,'Session prompt:',true);s.cancelConversation();s.onConversationAuthorized(1);assert.equal(s.authorized,true);assert.equal(s.cancelling,false);s.onConversationPrompt(1,1,'Session prompt:',true);assert.equal(s.activePrompt,1);s.password.text='fresh-session-response';s.submitResponse();s.cancelConversation();assert.equal(calls.cancels.length,1);
// A login success code in recovery mode never becomes a session-start signal.
({state:s,calls}=fixture());s.startConversation(true);s.onConversationFinished(1,2);assert.equal(s.sessionStarted,false);s.onSocketDisconnected();assert.equal(s.password.text,'');assert.equal(s.activeAttempt,0);
const event={key:999,modifiers:0,accepted:true};assert.equal(s.keyAction(event),false); // Ordinary typing is never swallowed.
assert.ok(source.includes('enabled: root.conversationAvailable && root.activeAttempt === 0 && !root.sessionStarted'));
assert.ok(source.includes('textFormat: Text.PlainText'));assert.ok(!source.includes('console.log'));
console.log('PASS: SDDM capability guard, fresh correlated prompts, cancel/authorization cutoff, stale events, PIN recovery never launches a session');
