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

For a fresh-profile test, first verify that `$HOME/.local/share/omarchy-himmelblau/sso-proof` does not exist. If it already exists, choose a different unused profile path for a new proof; do not treat reused cookies as fresh SSO evidence. Run in the work user's VM terminal:

```sh
chromium --user-data-dir="$HOME/.local/share/omarchy-himmelblau/sso-proof" --no-first-run chrome://extensions
```

Wait for the extension to install and enable before opening the intended Microsoft work application. Record whether account selection or further credentials were needed, then test reopening the browser. Existing cookies in an older profile cannot establish broker SSO. Never inspect or capture tokens, cookies, PRTs or authentication traffic as proof.

An optional dedicated-profile launcher is installed at `/usr/share/doc/omarchy-himmelblau/examples/sso-test`. Copy it to `~/sso-test` as the desktop user, using mode `0700`. Run `~/sso-test` for the extension page and `~/sso-test apps` for My Apps. Both commands intentionally reuse the same profile, including on reopening; the launcher does not itself establish that the profile is fresh. This convenience file is user-owned; the companion does not overwrite home-directory launchers during package upgrades.

## Background SSO permissions

For single-page applications, the extension needs host permission for the application
as well as Microsoft sign-in. While the application is active, open Linux Entra SSO
and enable **Background SSO** for that hostname, then approve the browser permission.
Test normal Exit/reopen without opening the popup or toggling accounts first.
See the [upstream application permission documentation](https://github.com/siemens/linux-entra-sso#single-page-applications).

The managed-device preference for this integration is background SSO on all HTTPS
applications when the browser/extension supports an explicit consented all-sites grant.
This increases the extension's site access; it does not remove MFA or Conditional Access.
Version `1.13.0` documents exact-site managed policies only. Do **not** deploy
`wellKnownApps: {"*": true}` with that version: its reconciliation can remove its own
all-sites grant. An upstream change is being prepared; until a supported release is
available, use explicit application-host grants. Browser approval cannot be supplied
by this companion package, and no browser profile databases are edited.
