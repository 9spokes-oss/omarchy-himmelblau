# SDDM login and PIN recovery

## Using the greeter

Enter your full work UPN, press Enter and answer each prompt as it appears. Secret prompts hide what you type. Complete any Microsoft browser or Authenticator step on your other device, then continue at the greeter.

Press **Ctrl+R**, or choose **Reset work PIN**, to replace your work PIN. Enter the new PIN twice and complete the authentication/MFA prompts. Recovery never signs you in: when the greeter reports the new PIN, press Enter to sign in with it.

**Esc** cancels the current attempt. The next attempt can start once the old helper has exited, at most five seconds later. After a failed or cancelled PIN reset, the next one is refused for 30 seconds; the wait doubles with each further failure up to 15 minutes. It resets after a successful reset or when SDDM or the display restarts.

Themes that use SDDM's older `login(user, password)` call still work when PAM asks for exactly one secret, the password. Any other prompt (MFA, a visible prompt, a retry) fails the attempt instead of reusing the password.

## Native integration

The greeter, daemon and helper must come from one build: a QML theme cannot add the conversation protocol to stock SDDM. `packaging/sddm-himmelblau` builds SDDM v0.21.0 with Arch's CMake 4 fix and the conversation patch; `provenance.json` pins the revisions and hashes. The closed upstream pull request sddm/sddm#2235 is a reference, not a dependency. The package ships the Qt6 daemon and greeter plus the optional Qt5 greeter and components; the Omarchy theme uses Qt6. Modified files keep SDDM's GPL/LGPL licences; see [third-party notices](../THIRD-PARTY-NOTICES.md).

Each attempt is bound to one operation and one user. Responses carry attempt and prompt numbers, so a cancelled or stale attempt cannot answer a later prompt or authorize a session. Every PAM prompt gets a fresh response. Informational and error text is shown apart from the input and cut at 8192 bytes. The bridge does not log prompts, responses or PAM text. Clearing response buffers is best-effort: copies inside Qt strings, socket buffers and QML are not wiped.

Normal login keeps SDDM's PAM authentication, account checks and session setup. PIN recovery runs the root SDDM helper with the fixed `sddm-himmelblau-pin-change` service (account and password only). The helper binds the configured UPN and its NSS identity, requires `pam_acct_mgmt` to succeed without changing an expired token, checks the identity again and then runs `pam_chauthtok`. The daemon only checks the binding file and name, so a slow NSS lookup cannot stall it; the helper repeats the full check. Recovery never opens a session or authorizes the session handshake.

Cancelling cannot undo a PIN change that PAM has already committed; the next login shows which PIN is current. The lock screen uses its own unprivileged helper, limited to the current user; the greeter does not reuse it.

## VM acceptance

Keep SSH and a local recovery console available. Before replacing anything, save the original SDDM package and signature, the previous companion package, the configuration, the companion state and the copied theme. Build both packages and run the native tests (`make native-test`, also run by the package's `check()`) before restarting the display manager.

Stop SDDM, then install the native and companion packages together. Replacing `sddm` can move locally modified PAM files to `.pacsave` and install upstream defaults: compare `/etc/pam.d/sddm` and `/etc/pam.d/sddm-autologin` with the saved copies and restore the reviewed policy before starting the greeter. Do not restore the PIN-change service from an old backup. On an existing installation run `sudo omarchy-himmelblau repair` and then `sudo omarchy-himmelblau doctor`; a fresh installation uses `setup`. `doctor` checks integration files, not the whole PAM stack or live authentication.

The companion depends on this SDDM package. Roll both back together from the recovery console with SDDM stopped, and restore their configuration, state and theme before starting the greeter. Rollback does not undo a completed PIN change.

Checks:

- Local recovery login through the rebuilt greeter.
- Work login with the current PIN opens a desktop.
- Cancelled login and cancelled PIN recovery leave the greeter running and create no work session.
- PIN recovery completes and creates no session.
- Login with the new PIN opens the same account and home directory.

Only the user types credentials, PINs and MFA responses. Record what the user reports separately from process and session metadata, and do not capture authentication screenshots or prompt text. Leave tenant policy, Intune enrollment and home naming unchanged.

## Recorded results

Recorded on 2026-11-07 with `sddm-himmelblau 0.21.0-1`, companion `0.3.0-1`, Omarchy `4.5.0-3` and Himmelblau `4.0.8-1`, using the Qt6 greeter. The PIN/MFA recovery cycle has not been run again on later builds, including `0.21.0-3` and the changes after it. The results cover the account and tenant used, not every MFA method or policy; Intune enrollment and offline sign-in were not tested.

- Local recovery login opened a Wayland desktop through the rebuilt greeter.
- The user confirmed login with the current PIN; session metadata showed a new work desktop.
- Cancelling login and PIN recovery stopped their helpers and left the greeter running with no work session. In that build the daemon waited about five seconds for the helper to exit.
- The user completed PIN recovery and its Microsoft/MFA prompts and saw the success message at the greeter. Metadata showed the same greeter and daemon, no work session and no remaining helper.
- The user then signed in with the new PIN and locked and unlocked the screen with it. Metadata showed a new work desktop, an unlocked locker and no SDDM restart; the lock clone and its managed files were unchanged.
- Root `doctor` and user `status` passed. UID, home mapping, Himmelblau configuration and global authentication-policy hashes matched the baseline.
