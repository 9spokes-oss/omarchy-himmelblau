// warp/plugin/Model.js under node:vm. Fixtures marked "captured" are real
// warp-cli 2026.8.2115.0 output; the rest follow the serde shapes in the
// binary (string or one-key-object reasons) and are not yet observed.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../warp/plugin/Model.js', import.meta.url), 'utf8')
  .replace(/^\.pragma library\s*$/m, '');
const Model = vm.createContext({});
vm.runInContext(source, Model);

const S = Model.STATES;
const status = (obj, code = 0) => Model.parseStatus(JSON.stringify(obj, null, 2), '', code);

// captured: connected host.
{
  const v = status({ status: 'Connected', reason: 'NetworkHealthy' });
  assert.equal(v.state, S.connected);
  assert.equal(v.label, 'Connected');
  assert.equal(v.detail, 'Network healthy');
  assert.equal(v.on, true);
  assert.equal(v.attention, false);
}

// captured: daemon socket absent, `-j status` exits 1 with a JSON error.
{
  const out = '{\n  "code": "FailedToConnectToDaemon",\n  "error": "No such file or directory (os error 2)"\n}\n';
  const v = Model.parseStatus(out, '', 1);
  assert.equal(v.state, S.daemonDown);
  assert.equal(v.attention, true);
  assert.equal(v.detail, 'warp-svc is not running (No such file or directory (os error 2))');
}

// captured: same without -j (stderr text).
{
  const err = 'Unable to connect to the CloudflareWARP daemon: No such file or directory (os error 2)\n\nMaybe the daemon is not running?\n';
  const v = Model.parseStatus('', err, 1);
  assert.equal(v.state, S.daemonDown);
  assert.match(v.detail, /^Unable to connect/);
}

// Runner exit 127: warp-cli not on PATH.
assert.equal(Model.parseStatus('', '', 127).state, S.notInstalled);

// Registration missing, as a string or a struct reason, under either status.
for (const reason of ['RegistrationMissing', { RegistrationMissing: 'DaemonStartup' }, { RegistrationMissing: { reason: 'ManualDeletion' } }]) {
  for (const s of ['Disconnected', 'Unable']) {
    const v = status({ status: s, reason });
    assert.equal(v.state, S.unregistered, JSON.stringify({ s, reason }));
    assert.equal(v.label, 'Not enrolled');
    assert.equal(v.attention, true);
    assert.equal(v.on, false);
  }
}

// The IPC error spelling (snake_case in the binary) as a status reason or an error code.
assert.equal(status({ status: 'Unable', reason: 'missing_registration' }).state, S.unregistered);
assert.equal(Model.parseStatus('{"code":"MissingRegistration","error":"x"}', '', 1).state, S.unregistered);

// Other daemon states.
assert.equal(status({ status: 'Connecting', reason: { PerformingHappyEyeballs: [1, 2] } }).state, S.connecting);
assert.equal(status({ status: 'Connecting', reason: { PerformingHappyEyeballs: [1, 2] } }).on, true);
assert.equal(status({ status: 'Disconnected', reason: 'ManualDisconnection' }).state, S.disconnected);
assert.equal(status({ status: 'Disconnected', reason: 'ManualDisconnection' }).detail, 'Manual disconnection');
assert.equal(status({ status: 'Paused', reason: null }).state, S.paused);
{
  const v = status({ status: 'Unable', reason: { TLSInterceptionBlockingDOH: { gateway_url: 'x' } } });
  assert.equal(v.state, S.unable);
  assert.equal(v.attention, true);
  assert.match(v.detail, /^TLS interception blocking DOH/);
}
{
  const v = status({ status: 'Disconnected', reason: { SettingsChanged: { a: 1, b: 2 } } });
  assert.equal(v.state, S.disconnected);
  assert.match(v.detail, /^Settings changed/);
}

// Unknown status: shown raw, with the badge.
{
  const v = status({ status: 'Hibernating', reason: 'Whatever' });
  assert.equal(v.state, S.unknown);
  assert.equal(v.attention, true);
  assert.equal(v.status, 'Hibernating');
}

// Garbage output.
assert.equal(Model.parseStatus('not json', '', 0).state, S.unknown);
assert.equal(Model.parseStatus('', 'boom', 3).detail, 'boom');

// humanize keeps acronyms readable.
assert.equal(Model.humanize('DisabledForWifi'), 'Disabled for Wi-Fi');
assert.equal(Model.humanize('InternalMtlsError'), 'Internal mTLS error');
assert.equal(Model.humanize('Already spaced text'), 'Already spaced text');

// Registration: only display names survive; ids, keys and fingerprints are dropped.
{
  const reg = Model.parseRegistration(JSON.stringify({
    id: 'r', device_id: 'd', public_key: 'k', managed: true,
    account: { type: 'Team', id: 'a', organization: 'example-team' },
    alternate_networks: [{ name: 'n', sha256_fingerprint: 'f' }],
  }));
  assert.deepEqual(JSON.parse(JSON.stringify(reg)), { organization: 'example-team', accountType: 'Team', managed: true });
  assert.equal(Model.parseRegistration('nope'), null);
}

// Tooltip and URL helpers.
assert.equal(Model.tooltip(status({ status: 'Connected', reason: 'NetworkHealthy' }), 'example-team'), 'Cloudflare WARP: Connected\nOrganization: example-team');
assert.equal(Model.firstUrl('Opening https://example.cloudflareaccess.com/warp now'), 'https://example.cloudflareaccess.com/warp');
assert.equal(Model.firstUrl('none'), '');

console.log('warp-model: ok');
