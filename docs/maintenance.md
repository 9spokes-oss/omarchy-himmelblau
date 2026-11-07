# Maintenance and validation

This integration changes authentication-sensitive system configuration and a user-owned lock plugin. Keep a local recovery account and an independent console available throughout setup and testing. Backups and rollback protect only the files owned or changed by this integration; preserve other local customizations and review conflicts.

Validate the layers independently:

1. Check package versions, service status, configuration, PAM/NSS ordering and local recovery login. Confirm administrative access requires authentication when enabled.
2. Complete the work account's first authentication interactively. Himmelblau's `aad-tool auth-test --name alice@example.com` exercises its resolver authentication channel; it does not prove the system PAM account stack, home creation, SDDM login, or Wayland session.
3. Confirm the Entra device object and, separately, any intended Intune enrollment and compliance state. An Entra join is not proof of Intune enrollment or compliance. Restrict tenant inspection and cleanup to the intended device.
4. Sign in through SDDM, verify the work user's home and desktop, then test local recovery and work PIN lock/unlock. Treat full lock-screen recovery as a separate acceptance test; SDDM still has no multi-step conversation UI.
5. Recheck after an Omarchy or Himmelblau update. A user-scoped lock clone is a snapshot of upstream code. The integration uses its own lock PAM service and leaves Omarchy's stock service untouched, but checks the stock source for compatibility. Compare upstream changes and use the integration's explicit repair workflow only after inspecting conflicts; unknown drift is refused.

`apply_policy = false` disables Himmelblau 4.0.8's own Intune enrollment path; it does not establish the absence of all tenant-side effects. A software HSM or emulated TPM is not evidence of physical hardware-backed protection. Offline authentication and conditional-access behavior require separate tests.

Omarchy supports user hooks, including `post-update`, and menu entries in a user-owned JSONC file. A hook runs as the user and should report drift rather than silently changing privileged PAM configuration. The menu file may contain existing customizations and comments; add any optional entry deliberately rather than replacing it. See the [Omarchy dotfiles manual](https://omarchy.org/manual/dotfiles/).

Before privileged rollback, disable the user clone and log out of **all sessions loading it**. Disabling alone does not stop `keepLoaded` code. Keep an independent local recovery console or SSH session open, and run root rollback there before any new clone session: removing the dedicated PAM service while its locker remains loaded can strand the next lock.

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

The helper uses `pam_chauthtok` for the current Unix identity and a fixed Himmelblau-only password service. It cannot unlock the desktop. A successful change replaces that user's Hello credential and invalidates associated cached credentials; configuration rollback does not undo it. Terminating the helper cannot reverse a daemon operation that has already committed. Keep recovery access available and use a separate normal authentication to determine which PIN is current.
