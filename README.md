# Omarchy Himmelblau

**Draft / work in progress.** Marketplace submission is withdrawn and release
downloads are unpublished while the integration is polished. The instructions below
are for development and controlled testing, not a supported deployment.

Experimental Microsoft Entra ID login, screen unlock and PIN recovery for Omarchy,
using [Himmelblau](https://himmelblau-idm.org/). The integration provides an Omarchy
lock clone, a conversation-capable SDDM build and an explicit system setup command.
Keep a local recovery account and an independent console when changing authentication.

## What works in the lab

On Omarchy `4.5.0-3`, Himmelblau `4.0.8-1` and Quickshell `0.3.2.r7.g2b4501a-1`:

- Work UPN + Hello PIN login, screen unlock and authenticated sudo.
- Multi-step PAM prompts, Microsoft/MFA messages and PIN replacement from both the
  locker and SDDM. Locker recovery retained the existing desktop and terminal.
  Successful PIN replacement requires a separate login/unlock.
- Intune enrollment with the existing Entra identity. The device is **noncompliant**
  because no compliance policy is assigned; it is unencrypted and uses software keys.
- Chromium SSO into My Apps and a historical profile-specific exit/reopen success
  after granting Background SSO permission. Current cold starts are unreliable;
  see the [browser diagnostic evidence](docs/intune-sso.md#browser-cold-start-diagnostics).

These are results from one dedicated QEMU/KVM lab and tenant, not support guarantees
for every MFA method, distribution, Conditional Access rule or future update. Offline
sign-in and hardware-backed protection are not established. See the detailed
[SDDM](docs/sddm.md) and [Intune/SSO](docs/intune-sso.md) evidence.

Plugin `0.5.2` also passed explicit migration, shell restart,
and a user-confirmed current-PIN lock/unlock while preserving the desktop and terminal.
Version `0.5.3` with native `0.21.0-4` completed controlled lab acceptance on
2026-11-08, including separate locker and fresh-SDDM PIN/MFA recovery cycles. See
the [version-specific results and limits](docs/maintenance.md#plugin-053-acceptance).

To prefill M365 Background SSO permissions for your company, use the installed generator with its actual SharePoint and OneDrive hostnames (they need not match your
email domain):

```sh
sudo /usr/lib/omarchy-himmelblau/m365-sso-policy.py --sharepoint-host example.sharepoint.com --onedrive-host example-my.sharepoint.com --install
```

Omit `sudo` and `--install` to preview JSON first; add `--app-host app.example.com`
for other applications. Then reload `chrome://policy` and approve the extension's
pending permission request. See [configuration and validation](docs/intune-sso.md#background-sso-permissions).

## Packages and setup

The `0.5.3` companion requires `sddm-himmelblau=0.21.0-4`. This is a reviewed native
backport, not a theme-only change or an official upstream SDDM release. Build both
packages from a clean committed source export using [the release procedure](docs/releasing.md).
Do not run the template PKGBUILDs directly or build packages as root.

Install Himmelblau separately after reviewing its [Arch package](https://github.com/himmelblau-idm/himmelblau/blob/4.0.8/platform/arch/PKGBUILD).
Its install hooks configure PAM/NSS, and it supplies browser policies, native messaging
and broker services. This companion's package installation does not run setup, join
Entra, enroll Intune, restart services or alter a user's home. The native SDDM package
replaces stock SDDM; coordinate it with the companion and preserve matching rollback
packages/configuration. See [SDDM installation](docs/sddm.md).

After reviewing the effective configuration and preparing independent recovery access:

```sh
sudo omarchy-himmelblau setup --domain example.com --user alice@example.com
sudo omarchy-himmelblau doctor
```

Use `setup --help` for the explicit administrator option. Authenticate the intended
account through Himmelblau to establish its identity before setup requires NSS
resolution. The first successful cloud authentication can create a tenant device.
Do not clear caches or rejoin an existing device to work around a setup failure.

After setup, install and activate the plugin from an unlocked desktop, then log out
and back in before testing the lock. Setup does not restart daemons, authenticate,
join a tenant or reload sessions. Root `doctor` checks files and sources; it does not
test daemon negotiation, authentication or sessions. User `status` performs the same
checks as `plugin-status`; each command refuses the wrong privilege level.

For a controlled `0.5.2` upgrade, install companion `0.5.3` and native SDDM
`0.21.0-4` together, run `sudo omarchy-himmelblau repair`, then update the plugin
while unlocked and restart the shell. Until repair, `doctor`, `setup` and `policy`
refuse the old theme. The old `0.5.2` plugin can still lock during this transition;
`status` and `plugin-status` report that it needs updating. See [maintenance](docs/maintenance.md).

## Install the Omarchy plugin

After installing and configuring the matching companion packages, run as the target
user while the desktop is unlocked. Run native `plugin add` **without `--enable`**,
review its trust prompt, and **answer No when asked to enable the plugin**. Omitting
`--enable` alone does not suppress that interactive prompt. Do not use `--yes` to
bypass the trust prompts.

```sh
omarchy plugin add https://github.com/9spokes-oss/omarchy-himmelblau.git
omarchy plugin list --json
```

Confirm the `9spokes.himmelblau-lock` entry has `enabled: false` before proceeding.
Then use the companion activation flow so dependency and conflict checks run first:

```sh
omarchy-himmelblau activate-plugin
omarchy-restart-shell
omarchy-himmelblau plugin-status
```

If another lock clone is enabled, activation is refused. Disable it with
`omarchy plugin disable <id>` while unlocked, then run `activate-plugin`.
If activation fails, stay unlocked, inspect `omarchy plugin list`, disable the fixed
plugin ID and restore the previous locker before restarting the shell.
Keep the session unlocked and Stay Awake during activation. A rescan alone does not
replace `keepLoaded` code; verify the shell is ready before locking.

The published plugin is `9spokes.himmelblau-lock`, a native clone of `omarchy.lock`.
It does not install packages, request sudo, join a tenant or overwrite existing user
configuration on installation. If the dedicated PAM file cannot be loaded
(`/etc/pam.d/omarchy-himmelblau-lock-password`), locking is refused with a desktop
notification. Idle/suspend callers can remain unlocked in this case; verify lock state.
After locking, a setup check runs once with a 15 s timeout; failure shows a warning
on the locked screen. Inspect that warning from recovery access before relying on it.

Plugin and companion versions must match; unreviewed Omarchy source or plugin
changes are refused. Update them together and repeat the unlocked activation checks.
The former `enable-user`, `enable-user --upgrade` and `activate-plugin --migrate`
commands are removed. Disable any old `<user>.lock` clone before activation.

To remove the plugin, first arrange a verified replacement locker for the work
account, or end the work desktop and use the local recovery account for the remaining
rollback. The unmodified stock PAM locker does **not** authenticate a Himmelblau PIN;
switching a live work desktop straight back to it can strand the next lock.

From an unlocked session, disable `9spokes.himmelblau-lock` through Omarchy, select
and verify the replacement, and restart the shell before removing the disabled plugin
with `omarchy plugin remove 9spokes.himmelblau-lock`. Old user clones must be
checked against the current companion before reuse. Do not remove system dependencies while any session still uses them. Plugin
removal does not remove credentials, undo a PIN change or delete tenant objects.

## Login, lock and PIN recovery

Press Enter to begin authentication, then answer each displayed prompt. Secret inputs
are masked and informational instructions remain visible. Escape cancels and leaves
the screen locked or at the greeter. **Reset work PIN** (`Ctrl+R`) starts the configured
account's recovery flow; it cannot itself unlock or create a desktop.
A failed or cancelled reset starts a 30 s backoff, doubling up to 15 min. The locker
keeps this in memory and clears it on unlock or successful reset; SDDM tracks it in
the daemon and clears it after successful reset. The greeter displays the remaining
wait. An unanswered lock-screen input prompt is cancelled after 60 s without user
activity; this timeout passed live in the lab. Silent MFA/network waits use backend
timeouts and are not cancelled by this prompt timer. A silent MFA wait beyond
60 seconds was not independently timed.

The locker helper is unprivileged, accepts no alternate user/service argument, and
binds its current UID to the root-managed configured UPN. SDDM repeats the same identity
checks in its privileged helper. Both require successful PAM account authorization
before token change (account management is not authentication), and reject identity
changes during that check. A cancelled
operation cannot undo a credential change already committed by the daemon.

## Security and maintenance

Privileged commands use fixed executable paths and an isolated Python interpreter.
The companion tracks exact managed files, identity mappings and native payload hashes;
unknown configuration, source or package drift is refused. Explicit `repair` upgrades
the recorded `0.5.2` theme; old 0.1/0.2 state is no longer migrated. See
[security policy](SECURITY.md), [maintenance](docs/maintenance.md)
and [release trust boundaries](docs/releasing.md).

A user-owned lock clone is a snapshot: review it after Omarchy updates. Quickshell's
normal unlock interface authenticates without PAM account management; the session
already belongs to that logged-in user. PIN recovery separately enforces account policy.
NSS remains a trusted identity authority, and shared kernel UIDs cannot be made
independent security principals by this integration.

Before root rollback, disable the clone and exit every session loading it. The command
refuses live or indeterminate graphical sessions by default. From independent recovery
access, `recover` restores only known interrupted transaction states. Root rollback
does not remove user clones or hooks, and disabling a plugin leaves loaded code alive
until the shell exits. Package removal
alone does not reverse setup, PIN changes, tenant objects or Himmelblau's package hooks.

Intune policy processing is an explicit opt-in (`policy --enable`); tenant scripts can
run with root privileges. The command does not restart services or perform tenant
operations; changing `apply_policy` does not prove compliance or undo enrollment
or previously applied policy. Enrollment does not prove compliance. The browser extension's
Online and compliance badges do not establish successful web SSO. All-site Background
SSO is a deliberate broader permission and still requires browser approval; read
[the current extension limitation](docs/intune-sso.md#background-sso-permissions).

## Development

```sh
make test
python3 tools/prepare-native.py /tmp/omarchy-native-source
```

CI compiles the actual patched Qt6 daemon/helper and Qt5 greeter and runs native security
and lifecycle proofs. Run checkout code unprivileged with
`python3 src/omarchy_himmelblau.py --development <command>`. The installed launcher
refuses `--development`, and development mode refuses root.
Use a disposable VM for privileged integration and actual authentication acceptance.

Companion code and Omarchy adaptations use [MIT](LICENSE). SDDM changes retain their
upstream GPL/LGPL licenses; see [third-party notices](THIRD-PARTY-NOTICES.md).
