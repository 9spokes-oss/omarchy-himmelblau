# Intune enrollment and browser SSO

Version `0.4.0` adds explicit local policy opt-in/out. Enrollment, the server's compliance label, measured security controls and browser access are separate results.

## VM acceptance — 2026-11-07

- Intune shows the enrolled Linux device with the original Entra device ID, the matching client enrollment ID, an enrollment time and a subsequent check-in. It reports the actual OS version, `Omarchy 4.5.0`, and manufacturer, `QEMU`.
- The device is **Not compliant**. Its Default Device Compliance Policy reports **Has a compliance policy assigned: Not compliant**, **Is active: Compliant**, and **Enrolled user exists: Compliant**. No Linux compliance policies or scripts were configured. The tenant-wide no-policy setting was observed as Compliant, but the device's actual per-setting result takes precedence; the discrepancy is unresolved. No tenant policy was changed to produce a compliant result.
- Intune reports **Encrypted: No**, matching the unencrypted Btrfs root. The VM also uses software key protection and lacks Secure Boot and a vTPM. These are recorded gaps, not the reported cause of the default-policy failure. Several hardware inventory fields remain unavailable or zero, so enrollment does not establish complete inventory support.
- As the work user, `aad-tool compliance-check` returned exit status `1`. That status alone is ambiguous; the portal independently establishes enrollment, check-in and the specific compliance result above.
- Chromium `155.0.8059.84-1` with Linux Entra SSO `1.13.0` initially signed in to My Apps without credentials in a profile verified absent before the test. A second fresh profile after Intune enrollment also worked initially but requested authentication after normal browser exit/reopen. Enabling **Background SSO for `myapps.microsoft.com`** fixed that tested restart: the user confirmed reopening without credentials, and metadata verified the persistent app-host permission. This supports the documented application-host permission requirement; it does not prove a captured network mechanism or guarantee that future tenant policies will never request MFA. No tokens, cookies, PRTs or authentication traffic were inspected.
- The existing desktop session and SDDM remained running during setup. The policy transition preserves UID/home mapping and the global PAM files. Automated validation passed 48 Python tests and both conversation test suites; Sol implementation, Terra evidence/build validation and Astra review were used.

## Enabling policy processing

Inspect the existing Entra identity and any matching Intune device before enrollment. Preserve recovery access, configuration and package backups. Review assigned Linux policies and scripts: Himmelblau's policy processing can execute tenant-delivered scripts, including as root. Do not clear identity caches or rejoin an already registered device just to enable Intune.

For an existing healthy managed installation:

```sh
sudo omarchy-himmelblau policy --enable
sudo omarchy-himmelblau doctor
sudo systemctl restart himmelblaud himmelblaud-tasks
```

The policy command changes only the local `apply_policy` setting and its managed state. It preserves original rollback data, rejects conflicting configuration layers and unresolved recovery journals, and restores configuration/state if a transition fails. Existing state without an explicit mode remains opted out. Package installation does not enroll or restart services.

Complete a subsequent online authentication as the intended work user. When using `aad-tool auth-test --name alice@example.com`, substitute your UPN and enter credentials only in the VM. Do not record the prompts or secrets. Himmelblau 4.0.8 attempts Intune enrollment for an already joined device when policy processing is enabled and no Intune enrollment is recorded. A successful login alone does not prove enrollment. Check the managed-device record, matching Entra device ID, check-in and assigned-policy results separately.

As the authenticated work user, `aad-tool compliance-check` requests a policy check. Its successful exit is not independent proof that every reported control is correctly measured. `sudo omarchy-himmelblau policy --disable` selects the local opt-out; applying that change requires restarting the daemons. It does not unenroll the device, remove tenant objects or undo previously applied policy.

## Interpreting compliance

The tenant setting for devices with no assigned compliance policy can mark a device **Compliant**. Record that as a default/no-policy result rather than evidence that disk encryption or another control passed. An enrollment-first lab should report the actual gaps and retain the existing tenant policy.

The pinned [Himmelblau 4.0.8 encryption check](https://github.com/himmelblau-idm/himmelblau/blob/4.0.8/src/policies/src/compliance_ext.rs) treats any nonempty `/etc/crypttab`, including comments, as encryption evidence. It also uses broad device-name/output substring checks. In the unencrypted test VM, a preserved, verified comments-only crypttab was normalized to empty; all four heuristic branches were then false. This is a VM-specific workaround, not disk encryption or a general measurement fix. Future package/configuration changes can restore the false positive. Password policy reporting also requires care: caching an expected setting is not measurement of existing passwords.

Microsoft's [documented Linux enrollment path](https://learn.microsoft.com/en-us/intune/device-enrollment/guide-linux) uses supported Ubuntu Desktop/RHEL releases, the Intune app and Edge. Omarchy with Himmelblau/Chromium is outside that documented platform path. Assess actual writable-disk encryption, software versus hardware key protection and assigned rules independently; do not spoof OS, TPM or encryption properties to satisfy a policy.

## Browser SSO

The tested Himmelblau Arch package already provides Chromium's managed extension policy, the native messaging host and the work-user D-Bus broker. The [Linux Entra SSO extension](https://chromewebstore.google.com/detail/linux-entra-sso/jlnfnnolkbjieggibinobhkjdfbpcohn), offered by `felix.moessbauer`, has ID `jlnfnnolkbjieggibinobhkjdfbpcohn`; the native host must allowlist that exact origin. Verify root ownership of its manifest/executable and that the extension is enabled in the intended work-user profile.

The extension uses the local broker and the desktop's Entra sign-in to obtain browser SSO. Ordinary browser cookies and Microsoft session/MFA policies still apply. Validate the work user's `himmelblau-broker.service` and D-Bus name `com.microsoft.identity.broker1`; checking the recovery account's user service does not test the work session. D-Bus activation may leave the broker inactive until requested.

The extension's **Online** indicator establishes native-host connectivity, not successful web sign-in. In version `1.13.0`, its compliance indicator does not gate the SSO header path; a Microsoft Conditional Access policy can separately require compliance. Do not attribute an authentication prompt to the badge alone. Account selection is compatible with SSO; a subsequent password, PIN or MFA challenge is a separate outcome.

Chromium's machine policy applies across profiles, but optional host-permission
approval and the extension's selected account belong to each profile. Before
checking permissions or comparing sign-in and restart results, open
`chrome://version` and verify **Profile Path**. Record the path and confirm that
all steps use that same profile; a policy or permission check in one profile does
not establish the state of another.

For a fresh-profile test, first verify that `$HOME/.local/share/omarchy-himmelblau/sso-proof` does not exist. If it already exists, choose a different unused profile path for a new proof; do not treat reused cookies as fresh SSO evidence. Run in the work user's VM terminal:

```sh
chromium --user-data-dir="$HOME/.local/share/omarchy-himmelblau/sso-proof" --no-first-run about:blank
```

In that dedicated-profile window, enter `chrome://extensions` in the address bar
or use the browser menu to manage extensions. Normal desktop Chromium command-line
launches can reject internal `chrome://` URLs; opening `about:blank` does not
guarantee that an internal page opens automatically.

Wait for the extension to install and enable before opening the intended Microsoft work application. Record whether account selection or further credentials were needed, then test reopening the browser. Existing cookies in an older profile cannot establish broker SSO. Never inspect or capture tokens, cookies, PRTs or authentication traffic as proof.

An optional dedicated-profile launcher is installed at `/usr/share/doc/omarchy-himmelblau/examples/sso-test`. Copy it to `~/sso-test` as the desktop user, using mode `0700`. Run `~/sso-test` to open a blank page with instructions to enter `chrome://extensions` manually, and `~/sso-test apps` for My Apps. Both commands intentionally reuse the same profile, including on reopening; the launcher does not itself establish that the profile is fresh. A normal desktop Chromium launch can open a different profile from this dedicated launcher. Verify **Profile Path** rather than assuming the windows share permissions or account selection. This convenience file is user-owned; the companion does not overwrite home-directory launchers during package upgrades.

## Background SSO permissions

For single-page applications, the extension needs host permission for the application
as well as Microsoft sign-in. While the application is active, open Linux Entra SSO
and enable **Background SSO** for that hostname, then approve the browser permission.
Test normal Exit/reopen without opening the popup or toggling accounts first.
See the [upstream application permission documentation](https://github.com/siemens/linux-entra-sso#single-page-applications).

The deployment preference is an explicit list of M365 application hosts, extended for
the organization's actual SaaS applications when needed. The extension's
[privacy documentation](https://github.com/siemens/linux-entra-sso/blob/main/PRIVACY.md)
distinguishes the login-provider permission used for normal Entra navigation from
the application-host permissions needed for background SPA refresh. This does not
guarantee that every navigation avoids MFA or Conditional Access requirements. The
My Apps restart result above is the observed lab evidence, not a traced explanation
of every request involved.

[`examples/chromium-m365-sso.json`](../examples/chromium-m365-sso.json) supplies exact
current and legacy entry hosts for Teams, Outlook, the M365 portal, Office web apps,
Forms, Copilot and My Apps. It is a starter application list, not Microsoft's network
endpoint inventory. Add the tenant's verified SharePoint and OneDrive hostnames
(for example, `tenant.sharepoint.com` and `tenant-my.sharepoint.com`) and any other
application's actual hostname before installation. Do not derive the tenant prefix
from an email domain without verifying it. Microsoft's [endpoint list](https://learn.microsoft.com/en-us/microsoft-365/enterprise/urls-and-ip-address-ranges)
and [web-app documentation](https://learn.microsoft.com/en-us/microsoft-365-apps/privacy/web-cookies)
identify the current and legacy entry hosts used here.

Use `tools/m365-sso-policy.py --sharepoint-host tenant.sharepoint.com
--onedrive-host tenant-my.sharepoint.com` to generate the complete JSON. The two
verified tenant hostnames are explicit inputs; different prefixes are accepted for
renamed or multi-geo environments. Repeat `--app-host app.example.com` for other
applications. Input validation requires exact ASCII DNS names and never performs
network discovery or infers tenant ownership from a hostname.

By default the script prints JSON and changes nothing. Add `--install` and run with
`sudo /usr/bin/python3 -I` for the fixed file
`/etc/chromium/policies/managed/90-omarchy-himmelblau-m365.json`. The installer checks
trusted nonsymlink paths, writes a new root-owned `0644` file, accepts identical
existing content, and refuses different target content or conflicting managed
`wellKnownApps` values. It does not silently replace existing policy.

Review existing `/etc/chromium/policies/managed/*.json` files for conflicting
`3rdparty.extensions.jlnfnnolkbjieggibinobhkjdfbpcohn` settings. Install the reviewed
JSON as a separate root-owned, mode `0644` policy file; do not overwrite an existing
policy blindly. Linux Chromium maps settings directly under that extension ID,
as documented by [Chromium](https://www.chromium.org/administrators/configuring-policy-for-extensions/#linux).
The companion does not install this optional example automatically.

True `wellKnownApps` entries request these permissions; they are not an exclusive
browser permission allowlist. Existing exact-site grants omitted from this map can
remain enabled. Review browser permissions separately when narrowing prior access;
this script does not audit or remove them.

After confirming the intended **Profile Path** in `chrome://version`, open
`chrome://policy`, reload policies, and verify the
extension's `wellKnownApps` value and **OK** status. Open or reopen the extension
popup; its badge can signal a pending managed policy update. If the popup shows
**A managed policy update is pending**, click **Apply** and approve the displayed
exact-site permissions. The absence of a pending update is ambiguous: permissions
may already be granted, or the extension may not have refreshed. Do not blindly rewrite
a policy whose value and status are correct. Managed policy prefills the request;
it does not bypass browser consent. Test My Apps, Teams, Outlook and the tenant's
SharePoint separately, including normal Exit/reopen without opening the popup or
toggling accounts first. Stop and record any credential challenge rather than
assuming SSO.

Version `1.13.0` supports this exact-site policy without an extension fork. Do **not**
deploy `wellKnownApps: {"*": true}` with that version: its reconciliation can remove
its own all-sites grant. The [all-HTTPS proposal](https://github.com/siemens/linux-entra-sso/pull/192)
was closed in favor of validating the existing policy mechanism. No browser
profile databases, cookies, tokens or PRTs are edited or inspected.

The generator passed 12 focused tests and independent reconciliation against the
unmodified extension `1.13.0`: an existing My Apps permission produced 18 additions
and no removals; simulating all 19 grants left no pending changes. In the VM, the
generated policy installed as root-owned `0644`, an identical second installation
succeeded, and the other policy files and desktop processes remained unchanged.
After user consent, metadata verified all 19 requested exact-origin grants in both
the active default profile and the inactive clean-test profile, without an
all-HTTPS grant. Subsequent default-profile cold starts gave mixed results; see
the diagnostic record below. Acceptance for the other M365 apps remains
unverified. The earlier successful My Apps restart test covered its individual
site grant in the tested profile. Pass/fail observations are scoped to the
recorded profile, not all Chromium profiles.

## Browser cold-start diagnostics

On 2026-11-08, Chromium `155.0.8059.84-1` with extension `1.13.0` was tested in
the verified default profile. With `DeveloperToolsAvailability` unset (default
`0`), worker inspection for the force-installed extension was unavailable even
with extension Developer mode enabled. A temporary lab-only value of `1` exposed
the inactive worker. Opening its inspector wakes the worker, so the resulting
metadata describes post-inspection state, not passive browser startup.

The actual-worker diagnostic confirmed the expected extension context, all 19
exact-host grants, no broad grant, one expected DNR rule with a nonempty SSO
header, and the SSO refresh alarm. No header or token values were emitted or
saved. My Apps succeeded after closing the debugger. Rule presence does not prove
that a request used the header or that the server accepted it.

Subsequent tests used normal browser Exit and a new browser process in the same
profile, without an extension popup, debugger or account toggle:

- Direct My Apps navigation reached an email step, then a security-key challenge.
- The next cold launch opened `about:blank`; after roughly 40 seconds, My Apps
  succeeded without credentials.
- The next immediate cold launch also succeeded without credentials.
- A further immediate cold launch showed an email form. A fresh My Apps
  navigation in that same process later succeeded without entering email or
  credentials, or toggling accounts.

These mixed outcomes support intermittent startup timing as a diagnostic lead.
They do not establish the responsible code, credential freshness or request-header
injection. A fixed sleep is not a robust fix. All 19 grants were retained; other
M365 applications remain unverified. The temporary debugging policy was removed;
after policy reload, Chromium showed no configured `DeveloperToolsAvailability`
policy. The M365 policy file was unchanged, and My Apps still opened. Normal idle
locking was restored (`enabled=true`, `stayAwake=false`) while the unlocked
desktop, original terminal and current browser remained running.
