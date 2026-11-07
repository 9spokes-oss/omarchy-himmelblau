# SDDM login and PIN recovery

Version `0.3.0-1` completed VM acceptance on 2026-11-07 with the reviewed `sddm-himmelblau 0.21.0-1` build, Omarchy `4.5.0-3` and Himmelblau `4.0.8-1`. This establishes the exercised account/tenant flow, not every MFA method or policy.

## Using the greeter

Enter your full work UPN, press Enter to start sign-in, and answer each prompt as it appears. The input follows PAM's visible or secret response mode. Complete any requested Microsoft browser or Authenticator step on the appropriate device, then continue at the greeter.

Choose **Reset work PIN** or press **Ctrl+R** for recovery. Enter the new PIN and confirmation, then complete the prompted authentication/MFA flow. When the greeter reports that the PIN was updated, press Enter to start a separate login with the new PIN. **Esc** cancels the current operation; helper shutdown can take about five seconds before another attempt is available.

## Native integration

Full conversation support requires a coordinated greeter, daemon and helper build. A QML theme alone cannot add the missing authentication protocol to the installed SDDM release. The separate `packaging/sddm-himmelblau` recipe builds a focused backport on SDDM v0.21.0 and the corresponding Arch CMake compatibility fix. Exact revisions and source hashes belong in its `provenance.json`. The upstream conversation PR is a reference, not an unmodified dependency.

The replacement package retains the Qt6 core and greeter plus the stock optional Qt5 greeter/components. The Omarchy theme selects Qt6. Native modifications retain SDDM's original GPL/LGPL file licenses; see [third-party notices](../THIRD-PARTY-NOTICES.md).

Each attempt fixes its operation and target identity. Responses carry attempt and prompt identifiers; a cancelled attempt cannot supply an answer to a later prompt or authorize a session. Every PAM prompt receives a fresh response, and informational messages are displayed separately from inputs. Passwords, PINs and MFA messages must not be written to authentication logs by the new bridge.

Normal login retains SDDM's PAM authentication, account checks and session setup. Explicit work-PIN recovery uses the existing trusted SDDM helper with a fixed password-only Himmelblau service. It performs `pam_chauthtok` and reports a distinct completion result. Recovery never starts a desktop, opens a session, or authorizes the normal session handshake. After changing the PIN, the user must start a separate normal login.

Cancelling a PIN change cannot undo an operation the daemon has already committed. The subsequent normal login determines which PIN is current. The locker helper remains unprivileged and restricted to its current Unix user; the greeter does not reuse it for arbitrary target users.

## VM acceptance

Keep SSH and an independent local recovery console available. Retain the original SDDM package and signature, the previous companion package, configuration, companion state and copied theme before replacement. Build and inspect both new packages and complete protocol tests before restarting the display manager. Login testing intentionally returns the disposable VM to the greeter.

Stop SDDM before installing the native and companion packages together. Replacing the package named `sddm` can move locally modified PAM files to `.pacsave` and install upstream defaults at their original paths. Before starting the new greeter, compare `/etc/pam.d/sddm` and `/etc/pam.d/sddm-autologin` with the saved configuration and deliberately preserve the reviewed local policy. Do not restore the new native PIN-change service from an old backup. Run `sudo omarchy-himmelblau repair` and `sudo omarchy-himmelblau doctor` for an existing managed installation; a fresh installation uses `setup` instead. Doctor checks integration files, not the complete global PAM stack or live authentication.

The new companion depends on the native SDDM package. Roll back the two packages together from the recovery console with SDDM stopped, then restore their matching configuration/state/theme before starting the greeter. Installing only the old SDDM binary would leave an incompatible companion/theme. Package/configuration rollback does not undo a completed PIN change.

Required checks:

- Local recovery login through the rebuilt greeter.
- Work-account login using the currently enrolled PIN, followed by a real desktop session.
- Cancelled login and cancelled PIN recovery remain at the greeter with no work session created.
- Complete the prompted work-PIN recovery flow; recovery success still creates no desktop session.
- A separate login with the new PIN opens the expected work desktop, with the same account and home-directory mapping.
- Native protocol tests reject stale responses, stale success/session acknowledgements, wrong-operation messages and overlapping attempts. They cover multiple prompts, visible/secret input, informational messages and failures without using real credentials.

Only the user enters work-account credentials, PINs and MFA responses. Record user-reported outcomes separately from process, service and session metadata. Do not capture authentication screenshots or prompt transcripts. Leave tenant policies, Intune enrollment and home naming unchanged during this milestone.

## Recorded results

- Local recovery login opened a Wayland desktop through the rebuilt greeter.
- The user confirmed current-PIN login; session metadata independently confirmed a new work desktop.
- Cancelling normal login and PIN recovery stopped their helpers and left the greeter running without a work session. Shutdown took approximately five seconds.
- The user completed PIN recovery and its Microsoft/MFA prompts, reporting the success message while remaining at the greeter. Independent metadata confirmed the same greeter and daemon, no work session, and no remaining work-authentication helper.
- The user then confirmed a separate login with the new PIN and screen lock/unlock with that PIN. Metadata showed a new active work desktop, an unlocked locker, and no SDDM restart. The existing enabled lock clone and its managed files remained intact.
- Root doctor and user status passed. UID, home mapping, Himmelblau configuration and existing global authentication-policy hashes matched the pre-milestone baseline. The independent local recovery console and SSH remained available.

Automated validation passed 38 Python tests, both JavaScript conversation suites, and both native CTest executables. The native checks exercise real socket framing and fresh responses, cancellation and session-acknowledgement gates, PAM prompt handling, nested socket-event ordering, and actual QObject teardown. Qt6 and the optional Qt5 greeter/components compiled independently; live acceptance used the Qt6 greeter. Intune enrollment, offline sign-in and other tenant policies were not tested by this milestone.
