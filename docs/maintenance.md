# Maintenance and validation

This integration changes authentication-sensitive system configuration and a user-owned lock plugin. Keep a local recovery account and an independent console available throughout setup and testing. Backups and rollback protect only the files owned or changed by this integration; preserve other local customizations and review conflicts.

Validate the layers independently:

1. Check package versions, service status, configuration, PAM/NSS ordering and local recovery login. Confirm administrative access requires authentication when enabled.
2. Complete the work account's first authentication interactively. Himmelblau's `aad-tool auth-test --name alice@example.com` exercises its resolver authentication channel; it does not prove the system PAM account stack, home creation, SDDM login, or Wayland session.
3. Confirm the Entra device object and, separately, any intended Intune enrollment and compliance state. An Entra join is not proof of Intune enrollment or compliance. Restrict tenant inspection and cleanup to the intended device.
4. Sign in through SDDM, verify the work user's home and desktop, then test local recovery and work PIN lock/unlock. Keep enrollment and multi-step MFA out of the lock screen unless the complete conversation has been validated.
5. Recheck after an Omarchy or Himmelblau update. A user-scoped lock clone is a snapshot of upstream code. The integration uses its own lock PAM service and leaves Omarchy's stock service untouched, but checks the stock source for compatibility. Compare upstream changes and use the integration's explicit repair workflow only after inspecting conflicts; unknown drift is refused.

`apply_policy = false` disables Himmelblau 4.0.8's own Intune enrollment path; it does not establish the absence of all tenant-side effects. A software HSM or emulated TPM is not evidence of physical hardware-backed protection. Offline authentication and conditional-access behavior require separate tests.

Omarchy supports user hooks, including `post-update`, and menu entries in a user-owned JSONC file. A hook runs as the user and should report drift rather than silently changing privileged PAM configuration. The menu file may contain existing customizations and comments; add any optional entry deliberately rather than replacing it. See the [Omarchy dotfiles manual](https://omarchy.org/manual/dotfiles/).

Before privileged rollback, disable the user clone and log out of **all sessions loading it**. Disabling alone does not stop `keepLoaded` code. Keep an independent local recovery console or SSH session open, and run root rollback there before any new clone session: removing the dedicated PAM service while its locker remains loaded can strand the next lock.
