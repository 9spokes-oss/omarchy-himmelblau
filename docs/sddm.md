# SDDM login and PIN recovery

## Using the greeter

Enter your full work UPN, press Enter and answer each prompt as it appears. Secret prompts hide what you type. Complete any Microsoft browser or Authenticator step on your other device, then continue at the greeter.

Press **Ctrl+R**, or choose **Reset work PIN**, to replace your work PIN. Enter the new PIN twice and complete the authentication/MFA prompts. Recovery never signs you in: when the greeter reports the new PIN, press Enter to sign in with it.

**Esc** cancels the current attempt. The next attempt can start once the old helper has exited, at most five seconds later. After a failed or cancelled PIN reset, the next one is refused for 30 seconds; the wait doubles with each further failure up to 15 minutes. It resets after a successful reset or when SDDM or the display restarts.

Themes that use SDDM's older `login(user, password)` call still work when PAM asks for exactly one secret, the password. Any other prompt (MFA, a visible prompt, a retry) fails the attempt instead of reusing the password.

## Native integration

The greeter, daemon and helper must come from one build: a QML theme cannot add the conversation protocol to stock SDDM. `packaging/sddm-himmelblau` builds SDDM v0.21.0 with Arch's CMake 4 fix, the conversation patch and a backport of upstream commit da0b64d (below); `provenance.json` pins the revisions and hashes. The closed upstream pull request sddm/sddm#2235 is a reference, not a dependency. The package ships the Qt6 daemon and greeter plus the optional Qt5 greeter and components; the Omarchy theme uses Qt6. Modified files keep SDDM's GPL/LGPL licences; see [third-party notices](../THIRD-PARTY-NOTICES.md).

Each attempt is bound to one operation and one user. Responses carry attempt and prompt numbers, so a cancelled or stale attempt cannot answer a later prompt or authorize a session. Every PAM prompt gets a fresh response. Informational and error text is shown apart from the input and cut at 8192 bytes. The bridge does not log prompts, responses or PAM text. Clearing response buffers is best-effort: copies inside Qt strings, socket buffers and QML are not wiped.

Normal login keeps SDDM's PAM authentication, account checks and session setup. PIN recovery runs the root SDDM helper with the fixed `sddm-himmelblau-pin-change` service (account and password only). The helper binds the configured UPN and its NSS identity, requires `pam_acct_mgmt` to succeed without changing an expired token, checks the identity again and then runs `pam_chauthtok`. The daemon only checks the binding file and name, so a slow NSS lookup cannot stall it; the helper repeats the full check. Recovery never opens a session or authorizes the session handshake.

SDDM 0.21.0's greeter quits as soon as a login succeeds, and its compositor tears down while the session helper switches VTs. If the greeter session's VT cleanup lands inside that switch, the switch is dropped: the screen stays black for 30 s, SDDM logs `Session started false`, and 60 s later it kills the session (`sddm-helper exited with 2`) and shows the greeter again. In the fresh-install lab it hit the first sign-in after 34 hours at the greeter, whose compositor took 510 ms to stop instead of the usual 200 ms. `0002-greeter-keep-running.patch` (upstream da0b64d, not in 0.21.0) keeps the greeter running until the daemon stops it 5 s after the session starts. The daemon stops the greeter's helper with SIGTERM, so every login now ends the greeter with `sddm-helper exited with 255` and `Greeter stopped. SDDM::Auth::HELPER_OTHER_ERROR`; `Greeter::onHelperFinished()` ignores that status, so it doesn't touch the user session. The theme receives `ConversationFinished(Login)` while still running and shows "Starting session…". On 2026-11-21 a second fresh-install VM (Omarchy `4.5.0-3`, updated, with `sddm-himmelblau 0.21.0-5`, companion `0.6.0-1` and Himmelblau `4.0.8-3` built with makepkg) confirmed the new order with the stock Omarchy theme and a local password account, before setup: on two sign-ins SDDM logged `Session started true` within 0.1 s of authentication and `Greeter stopping...` 4.8–5.3 s later. The first session was still active 90 s on, a wrong password left the greeter running, and logging out brought it back within a second. On 2026-11-22, after `setup` on the same VM, an Entra sign-in with UPN and Hello PIN through the `omarchy-himmelblau` theme showed the same order: `Session started true` 0.52 s after authentication and `Greeter stopping...` 5.24 s later, with the session still active after 78 s. This confirms the mechanism, not that the rare race can no longer occur. Upstream reports: sddm/sddm#2043, #2129 and the analysis in PR #2204.

Cancelling cannot undo a PIN change that PAM has already committed; the next login shows which PIN is current. The lock screen uses its own unprivileged helper, limited to the current user; the greeter does not reuse it.

## Upstream watch

`sddm-himmelblau` replaces Arch's `sddm`, so its fixes are ours to carry. The daily [Upstream SDDM workflow](../.github/workflows/upstream-sddm.yml) runs `tools/check-upstream-sddm.py` (locally: `python3 tools/check-upstream-sddm.py`, add `--build` on a machine with the `native` job's build dependencies). It checks:

- **Arch's packaging** (`archlinux/packaging/packages/sddm` on gitlab.archlinux.org). It replays Arch's `prepare()` (the `v$pkgver` tag plus Arch's cherry-picks), applies our series with the PKGBUILD's `patch --fuzz=0` and `--forward`, then builds Qt6 and Qt5 and runs the native proofs. A patch whose upstream commit the tree already has is skipped: 0000 is our copy of Arch's cherry-pick 228778c, 0002 is upstream da0b64d. The baseline is `arch_packaging_commit` in `provenance.json`: a different `pkgver`, `pkgrel` or cherry-pick list from that commit's PKGBUILD alerts, as does any `source=()` or `prepare()` step the check cannot replay, or a moved `baseline_tag`.
- **Upstream releases.** Any `sddm/sddm` tag newer than `baseline_tag` alerts, with whether our series applies to it.
- **Upstream `develop`**, informational only. 0001 does not apply there (two `Display.h` hunks were rejected on 2026-11-22), and that changes with every upstream refactor, so a daily alert would be noise. The job summary shows the rejected hunks and the commits since our release; read it when rebasing or proposing the protocol upstream.

An alert fails the job and opens one issue titled "Native SDDM patches need attention against upstream" with the `upstream-sddm` label, holding the versions and commits checked, the failing patch, its rejected hunks or the build or proof output. A later run comments only when the alerts change, and the first passing run closes the issue. A run that cannot check (network, clone) fails without touching the issue.

When it fires:

- **Arch bumped `pkgrel` or cherry-picks**: review what Arch changed. Carry any new fix into our series as a patch (as 0000 carries 228778c), bump our `pkgrel` and release. Then set `arch_packaging_commit` to Arch's new commit; that clears the drift alert.
- **A patch no longer applies, or the build or proofs fail**: rebase the series onto the tree the report names, update `provenance.json` and release a new `pkgrel`.
- **A new upstream release**: rebase onto it, set `baseline_tag`, `baseline_commit` and the archive hash, and release. The job stays red until then: that is intended, since a release usually carries fixes our package lacks.

## VM acceptance

Keep SSH and a local recovery console available. Before replacing anything, save the original SDDM package and signature, the previous companion package, the configuration, the companion state and the copied theme. Build both packages and run the native tests (`make native-test`, also run by the package's `check()`) before restarting the display manager.

Stop SDDM, then install the native and companion packages together. Replacing `sddm` can move locally modified PAM files to `.pacsave` and install upstream defaults: compare `/etc/pam.d/sddm` and `/etc/pam.d/sddm-autologin` with the saved copies and restore the reviewed policy before starting the greeter. On Omarchy, the saved copies are Omarchy's own edits: its installer drops the gnome-keyring lines from `sddm` and moves the faillock reset in `sddm-autologin`. `setup` puts them back: when the installed file is still the native package's own and its `.pacsave` is exactly Omarchy's edit of it, it writes it over that file (keeping its owner and mode) and removes the `.pacsave`, in the setup transaction. Any other `.pacsave` is refused for review by hand. Rollback leaves Omarchy's edits in place, and `doctor` reports a `.pacsave` that reappears. Do not restore the PIN-change service from an old backup. On an existing installation run `sudo omarchy-himmelblau repair` (same companion version) or `upgrade` (from the previous release, see [upgrading between releases](maintenance.md#upgrading-between-releases)), then `sudo omarchy-himmelblau doctor`; a fresh installation uses `setup`. `doctor` checks integration files, not the whole PAM stack or live authentication.

The companion depends on this SDDM package. Roll both back together from the recovery console with SDDM stopped, and restore their configuration, state and theme before starting the greeter. Rollback does not undo a completed PIN change.

Checks:

- Local recovery login through the rebuilt greeter.
- Work login with the current PIN opens a desktop.
- Cancelled login and cancelled PIN recovery leave the greeter running and create no work session.
- PIN recovery completes and creates no session.
- Login with the new PIN opens the same account and home directory.

Only the user types credentials, PINs and MFA responses. Record what the user reports separately from process and session metadata, and do not capture authentication screenshots or prompt text. Leave tenant policy, Intune enrollment and home naming unchanged.

## Recorded results

Recorded on 2026-11-07 with `sddm-himmelblau 0.21.0-1`, companion `0.3.0-1`, Omarchy `4.5.0-3` and Himmelblau `4.0.8-1`, using the Qt6 greeter. The separate 2026-11-08 acceptance for companion `0.5.3-1` and native `0.21.0-4`, including repeated PIN/MFA recovery, is recorded in [maintenance](maintenance.md#plugin-053-acceptance). The results cover the account and tenant used, not every MFA method or policy; Intune enrollment and offline sign-in were not tested.

- Local recovery login opened a Wayland desktop through the rebuilt greeter.
- The user confirmed login with the current PIN; session metadata showed a new work desktop.
- Cancelling login and PIN recovery stopped their helpers and left the greeter running with no work session. In that build the daemon waited about five seconds for the helper to exit.
- The user completed PIN recovery and its Microsoft/MFA prompts and saw the success message at the greeter. Metadata showed the same greeter and daemon, no work session and no remaining helper.
- The user then signed in with the new PIN and locked and unlocked the screen with it. Metadata showed a new work desktop, an unlocked locker and no SDDM restart; the lock clone and its managed files were unchanged.
- Root `doctor` and user `status` passed. UID, home mapping, Himmelblau configuration and global authentication-policy hashes matched the baseline.
