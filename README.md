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
Version `0.6.0` passed a fresh-install acceptance on 2026-11-18, from a new Omarchy
install through setup, greeter sign-in and PIN lock/unlock, and on 2026-11-20 with the
plugin installed from GitHub. See its
[results and limits](docs/maintenance.md#060-fresh-install-acceptance).

To prefill M365 Background SSO permissions for your company, use the installed generator with its actual SharePoint and OneDrive hostnames (they need not match your
email domain):

```sh
sudo /usr/lib/omarchy-himmelblau/m365-sso-policy.py --sharepoint-host example.sharepoint.com --onedrive-host example-my.sharepoint.com --install
```

Omit `sudo` and `--install` to preview JSON first; add `--app-host app.example.com`
for other applications. Then reload `chrome://policy` and approve the extension's
pending permission request. See [configuration and validation](docs/intune-sso.md#background-sso-permissions).

## Packages and setup

The `0.6.2` companion requires `sddm-himmelblau=0.21.0-5`. This is a reviewed native
backport, not a theme-only change or an official upstream SDDM release. Build both
packages from a clean committed source export using [the release procedure](docs/releasing.md).
Do not run the template PKGBUILDs directly or build packages as root.

Install Himmelblau 4.0.8 or later from the [AUR](https://aur.archlinux.org/packages/himmelblau).
Its install script configures NSS, and the package supplies browser policies, native
messaging and broker services. On Omarchy, don't run `aad-tool configure-pam`: its
layout lets a successful Entra sign-in skip `pam_shells`, `pam_nologin` and
`pam_faillock`. `setup` configures system PAM instead. It patches
`/etc/pam.d/system-auth` so Entra users get the same lockout and account checks as local
ones, and requires `/etc/pam.d/system-login` to stay pristine. Both files, and any
`.pacnew` it adopts, must be owned by root, writable only by root and free of ACLs, in a
`/etc/pam.d` only root can write. It accepts pambase
`20260616-1` as installed, with or without the lockout tuning Omarchy's installer applies
to `system-auth`, or exactly `aad-tool configure-pam`'s output on either (4.0.8's in both
files, or in `system-auth` alone once aad-tool skips `system-login`), which it undoes; anything else is refused. `rollback` restores the version setup found, never
aad-tool's layout. After a pambase upgrade, `doctor` reports the `.pacnew` and `repair` moves to a
supported release; see [maintenance](docs/maintenance.md#system-pam-and-pambase-upgrades).
This companion's package installation does not run setup, join
Entra, enroll Intune, restart services or alter a user's home. The native SDDM package
replaces stock SDDM; coordinate it with the companion and preserve matching rollback
packages/configuration. See [SDDM installation](docs/sddm.md).

SDDM autologin is unsupported: it skips the sign-in screen, so the Entra user never gets
to sign in, and an autologin session's lock screen has no failed-attempt lockout.
Omarchy's installer turns it on for disk-encrypted installs, in
`/etc/sddm.conf.d/autologin.conf`; remove the
`[Autologin]` `User=` line before setup. `setup` refuses while autologin is configured,
and `doctor` reports it if it returns.

Before setup, the account must have signed in once through Himmelblau: until then
Himmelblau answers a name lookup with a computed entry but not the reverse lookup, and
`setup` refuses. Set `domain` in `/etc/himmelblau/himmelblau.conf` (setup adds the rest),
then:

```sh
sudo systemctl enable --now himmelblaud himmelblaud-tasks
aad-tool auth-test --name alice@example.com
```

The user completes MFA and enrols a Hello PIN. This first sign-in can create a tenant
device. Then, after reviewing the effective configuration and preparing independent
recovery access:

```sh
sudo omarchy-himmelblau setup --domain example.com --user alice@example.com
sudo omarchy-himmelblau doctor
```

Use `setup --help` for the explicit administrator option. Do not clear caches or rejoin
an existing device to work around a setup failure.

After setup, [install the plugin](#install-the-omarchy-plugin), then log out and back
in before testing the lock. Setup does not restart daemons, authenticate,
join a tenant or reload sessions. Root `doctor` checks files and sources; it does not
test daemon negotiation, authentication or sessions. User `status` performs the same
checks as `plugin-status`; each command refuses the wrong privilege level.

### Upgrading

Install the `omarchy update` hook once, as the desktop user:

```sh
omarchy hook install post-update /usr/share/omarchy-himmelblau/post-update/omarchy-himmelblau
```

Then `omarchy update` finishes a companion upgrade from the running desktop. After its
packages and shell restart, the hook:

1. runs `sudo omarchy-himmelblau upgrade` (using the update's sudo authorization);
2. updates the lock plugin, but only when the plugin's origin holds exactly the new
   companion release, and restarts the shell only if the update installed it;
3. marks a reboot as required when `sddm-himmelblau` changed, so `omarchy update`
   offers one at the end.

Anything it can't do appears in the update output and as a desktop notification. A
`pacman` hook also prints the step left after any upgrade of either package, including
a plain `pacman -Syu`.

Without the hook, after upgrading both packages run `sudo omarchy-himmelblau upgrade`,
then `omarchy plugin update 9spokes.himmelblau-lock`, `omarchy-restart-shell` and
`omarchy-himmelblau status`. If `sddm-himmelblau` changed (`omarchy-himmelblau
reboot-required` exits 0), run `upgrade` before rebooting, and reboot rather than just
log out.

`upgrade` accepts state from any earlier release that uses the current state format
(listed in `assets/patches.json`), so skipped releases are fine, and only while every
managed file is exactly what that release wrote. It rewrites only what changed between
the two releases (the theme copy's `Main.qml` and the lock PAM service) and records the
new version, in one journalled transaction. It runs from a live desktop unless it
replaces the lock PAM service; then log out of every graphical session and run it from
a text console (the login screen may stay up). No release so far has changed that
service.

It refuses anything else: an unlisted release, drift, or a pambase upgrade in the same
update. Then run `sudo omarchy-himmelblau rollback` with the new companion, then
`setup`. Until `upgrade` runs, `doctor`, `setup`, `repair` and `policy` refuse the old
state and point to it. An older listed plugin release passes the lock's setup check,
but `status` asks for the update. State from `0.6.0` and earlier is refused: roll it
back, then set up again. See [maintenance](docs/maintenance.md#upgrading-between-releases).

## Install the Omarchy plugin

Run `setup` first. Then, as the target user:

```sh
omarchy plugin add https://github.com/9spokes-oss/omarchy-himmelblau.git --enable
omarchy-himmelblau status
```

Review the trust prompt rather than passing `--yes`. Log out and back in before relying
on the lock.

- **One lock plugin at a time.** Disable any other lock plugin first
  (`omarchy plugin disable <id>`): when two are enabled, Omarchy picks one arbitrarily.
  `status` reports a second one.
- **Before setup, it's a local-password lock.** Until `setup` has installed
  `/etc/pam.d/omarchy-himmelblau-lock-password`, the plugin locks with Omarchy's stock
  password service and says so on the lock screen. An Entra account can't unlock it
  that way, so run `setup` before relying on it.
- **Lock-time check.** After locking, a setup check runs once with a 15 s timeout; a
  failure shows a warning on the locked screen.

The plugin `9spokes.himmelblau-lock` is a clone of `omarchy.lock`. It doesn't install
packages, ask for sudo, join a tenant or overwrite user configuration. Plugin and
companion versions must match, and `status` refuses unreviewed Omarchy source or plugin
changes, so update them together.

To remove the plugin, first arrange a verified replacement locker for the work
account, or end the work desktop and use the local recovery account for the remaining
rollback. The unmodified stock PAM locker does **not** authenticate a Himmelblau PIN;
switching a live work desktop straight back to it can strand the next lock.

From an unlocked session, disable `9spokes.himmelblau-lock`, enable and verify the
replacement, and restart the shell before `omarchy plugin remove 9spokes.himmelblau-lock`.
Don't remove system packages while a session still uses them. Removing the plugin doesn't
remove credentials, undo a PIN change or delete tenant objects.

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
unknown configuration, source or package drift is refused. Explicit `upgrade` moves
an earlier release's setup to this one; old 0.1/0.2 state is no longer migrated. See
[security policy](SECURITY.md), [maintenance](docs/maintenance.md)
and [release trust boundaries](docs/releasing.md).

A user-owned lock clone is a snapshot: review it after Omarchy updates. Quickshell's
normal unlock interface authenticates without PAM account management; the session
already belongs to that logged-in user. PIN recovery separately enforces account policy.
NSS remains a trusted identity authority, and shared kernel UIDs cannot be made
independent security principals by this integration.

Before root rollback, disable the clone and exit every session loading it. The command
refuses live or indeterminate graphical sessions by default. From independent recovery
access, `recover` undoes an interrupted root change, restoring each file's exact bytes,
owner and mode only if every target is still at its journalled before or after value. Root rollback
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
