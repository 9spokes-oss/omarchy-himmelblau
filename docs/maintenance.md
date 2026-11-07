# Maintenance and validation

## Plugin 0.5.2 acceptance

On 2026-11-07, the plugin at source commit
`6988919168b3f13c159805fa1d200cdd6982edb3` was installed with native `plugin add`:
the trust prompt was accepted and the enable prompt declined. Matching companion
`0.5.2-1` and native SDDM `0.21.0-3` packages were already installed and checked.
Explicit `activate-plugin --migrate` and the supported unlocked shell restart passed.
Only the fixed plugin remained enabled; the prior clone and its files were retained.

The new shell reported PAM, companion and recovery prerequisites ready and subsequently
held a secure lock. The user confirmed current-PIN unlock returned to the same desktop
with the existing terminal open. Compositor and terminal process identities were
preserved across migration, and the original automatic idle-lock setting was restored.

This acceptance does not establish a new PIN/MFA recovery cycle after the security
hardening or a fresh session through the newly installed SDDM daemon. Earlier
milestones exercised those flows; their version-specific results remain separate.
Releases and marketplace submission are currently draft/withdrawn while polishing.

Before the next native package publication, remove the added hard `RLIMIT_CORE=0`
limit from the normal-login helper path: child desktop sessions can inherit it.
Retain helper dump protection, keep the PIN-only helpers' limits separate, and test
child-limit inheritance under a new package identity. This affects crash-dump
configuration, not authentication correctness; piped core collectors can ignore
this limit. Existing `0.5.2` assets and the running desktop are not changed by this
follow-up.

## Validation procedure

This integration changes authentication-sensitive system configuration and a user-owned lock plugin. Keep a local recovery account and an independent console available throughout setup and testing. Backups and rollback protect only the files owned or changed by this integration; preserve other local customizations and review conflicts.

Validate the layers independently:

1. Check package versions, service status, configuration, PAM/NSS ordering and local recovery login. Confirm administrative access requires authentication when enabled.
2. Complete the work account's first authentication interactively. Himmelblau's `aad-tool auth-test --name alice@example.com` exercises its resolver authentication channel; it does not prove the system PAM account stack, home creation, SDDM login, or Wayland session.
3. Confirm the Entra device object and, separately, any intended Intune enrollment and compliance state. An Entra join is not proof of Intune enrollment or compliance. Restrict tenant inspection and cleanup to the intended device.
4. Sign in through SDDM, verify the work user's home and desktop, then test local recovery and work PIN lock/unlock. Treat lock-screen recovery and the version `0.3.0` native SDDM conversation/reset flow as separate acceptance tests; see [the SDDM guide](sddm.md).
5. Recheck after an Omarchy or Himmelblau update. A user-scoped lock clone is a snapshot of upstream code. The integration uses its own lock PAM service and leaves Omarchy's stock service untouched, but checks the stock source for compatibility. Compare upstream changes and use the integration's explicit repair workflow only after inspecting conflicts; unknown drift is refused.

`apply_policy = false` disables Himmelblau 4.0.8's own Intune enrollment path; it does not establish the absence of all tenant-side effects. A software HSM or emulated TPM is not evidence of physical hardware-backed protection. Offline authentication and conditional-access behavior require separate tests.

Omarchy supports user hooks, including `post-update`, and menu entries in a user-owned JSONC file. A hook runs as the user and should report drift rather than silently changing privileged PAM configuration. The menu file may contain existing customizations and comments; add any optional entry deliberately rather than replacing it. See the [Omarchy dotfiles manual](https://omarchy.org/manual/dotfiles/).

Before privileged rollback, disable the user clone and log out of **all sessions loading it**. Disabling alone does not stop `keepLoaded` code. Keep an independent local recovery console or SSH session open, and run root rollback there before any new clone session: removing the dedicated PAM service while its locker remains loaded can strand the next lock.

Native SDDM replacement has its own package rollback. The companion's root rollback does not remove or downgrade the native display manager. Restore compatible SDDM/companion packages and their matching configuration together using the separate SDDM procedure; do not mix a conversation theme with an incompatible daemon.

## Testing recovery without ending the desktop

Record the graphical session ID, compositor PID/start time, and an existing application's PID/start time before upgrading. Keep a local recovery console or SSH session available. Use Omarchy's Stay Awake mode during activation, record its original setting, and restore it after testing.

Run the package checks and headless QML syntax checks before activation. Upgrade the clone only while unlocked, then run the supported `omarchy-restart-shell`. Do not restart SDDM, log out, reboot, or kill the compositor. Confirm the new shell is ready and the recorded session and applications survived before locking.

Exercise these cases separately:

1. Normal sign-in using the current PIN.
2. Cancel a normal authentication prompt and confirm the screen stays locked.
3. Open Reset work PIN and cancel at its first prompt, before entering a replacement PIN. The real-libpam smoke test can also close helper stdin at this first prompt; Himmelblau 4.0.8 has not changed credentials at that point.
4. Complete Reset work PIN with a new PIN, confirmation, and the tenant's MFA flow. Verify that successful PIN replacement leaves the screen locked, then authenticate with the new PIN.
5. Confirm the original session, compositor, and application processes still exist, check integration status, and restore the original idle setting.

Only the user enters credentials and MFA responses. Do not capture authentication screenshots, prompt transcripts, passwords, PINs, or codes. Record user-reported results separately from nonsecret process and lock-state evidence. Synthetic conversation tests cover failure and race conditions without consuming real invalid-PIN attempts.

The helper binds the current Unix identity to the root-managed configured UPN, runs the dedicated PAM account phase, and calls `pam_chauthtok` only after account and identity checks succeed. It cannot unlock the desktop. A successful change replaces that user's Hello credential and invalidates associated cached credentials; configuration rollback does not undo it. Terminating the helper cannot reverse a daemon operation that has already committed. Keep recovery access available and use a separate normal authentication to determine which PIN is current.

## Identity, provenance and interrupted changes

Version `0.5.0` stores the configured UPN, canonical NSS name and UID. Every privileged
status/repair checks their forward and reverse mappings. Administrative sudo rules use
the canonical name rather than a persistent numeric-UID grant. An explicit `repair`
can migrate an unchanged older managed rule after those checks. NSS remains trusted:
round trips cannot establish global uniqueness or prevent a provider from assigning
one kernel UID to multiple identities. Do not change identity mapping on a deployed
machine without a separate migration plan.

Both PIN recovery services bind to `/etc/omarchy-himmelblau/recovery-account` and deny
missing, malformed, unsafe or mismatched identity policy before requesting a PIN.
Only the configured principal is eligible. An account denial, `PAM_IGNORE`, expired
account requiring an automatic token change, or changed PAM user does not authorize
recovery. SDDM's recovery check intentionally avoids normal PAM expired-token side effects.

The companion checks native executables against a payload manifest built into the
separate companion package from the reviewed native artifact. Package name/version
alone is insufficient. This detects a native package rebuilt or modified after the
companion's accepted build. Trust the release source/checksums and package installation
channel; the manifest is not a package signature and does not defend against root
replacing both trusted packages. See [release builds](releasing.md).

Configuration replacements fsync file contents and parent directories, and record
per-entry transaction progress. If an operation is interrupted, preserve the root
state/journal and run `sudo omarchy-himmelblau recover` from recovery access. It accepts
only known before/after bytes; unknown edits need manual investigation. Default rollback
and recovery refuse live or indeterminate graphical/locker sessions. The explicit
`--recovery-only` override is for an operator at an independent console who has ensured
that restoring configuration cannot strand a dependent session. It does not stop sessions.
