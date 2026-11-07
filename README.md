# Omarchy Himmelblau

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
- Chromium SSO into My Apps, including normal browser exit/reopen after granting
  Background SSO permission for the application host.

These are results from one dedicated QEMU/KVM lab and tenant, not support guarantees
for every MFA method, distribution, Conditional Access rule or future update. Offline
sign-in and hardware-backed protection are not established. See the detailed
[SDDM](docs/sddm.md) and [Intune/SSO](docs/intune-sso.md) evidence.

## Packages and setup

The `0.5.1` companion requires `sddm-himmelblau=0.21.0-2`. This is a reviewed native
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

## Install the Omarchy plugin

After installing and configuring the matching companion packages, run as the target
user while the desktop is unlocked. Native `plugin add` is intentionally used **without
`--enable`** so dependency and conflict checks happen before activation:

```sh
omarchy plugin add https://github.com/9spokes-oss/omarchy-himmelblau.git
omarchy-himmelblau activate-plugin
omarchy-restart-shell
omarchy-himmelblau plugin-status
```

If an earlier companion-created user lock clone is enabled, replace the activation
command with `omarchy-himmelblau activate-plugin --migrate`. It accepts only the known
managed clone, disables it through Omarchy, verifies one selected lock implementation,
and preserves its files/state. Unknown or multiple enabled lock clones are refused.
Keep the session unlocked and Stay Awake during activation. A rescan alone does not
replace `keepLoaded` code; verify the shell is ready before locking.

The published plugin is `9spokes.himmelblau-lock`, a native clone of `omarchy.lock`.
It does not install packages, request sudo, join a tenant or overwrite existing user
configuration on installation. A missing/drifted companion PAM prerequisite prevents
locking; missing PIN helper support cannot unlock the screen. Keep the recovery
console available and inspect any prerequisite error before relying on the locker.

The original `enable-user` / `enable-user --upgrade` workflow remains available for
managed user-specific clones. Do not enable it alongside the published plugin. Plugin
and companion versions must match; unreviewed Omarchy source or plugin changes are
refused. Update them together and repeat the unlocked activation checks.

To remove the plugin, first arrange a verified replacement locker for the work
account, or end the work desktop and use the local recovery account for the remaining
rollback. The unmodified stock PAM locker does **not** authenticate a Himmelblau PIN;
switching a live work desktop straight back to it can strand the next lock.

From an unlocked session, disable `9spokes.himmelblau-lock` through Omarchy, select
and verify the replacement, and restart the shell before removing the disabled plugin
with `omarchy plugin remove 9spokes.himmelblau-lock`. A previously managed user clone
is preserved by migration, but must be checked against the current companion before
reuse. Do not remove system dependencies while any session still uses them. Plugin
removal does not remove credentials, undo a PIN change or delete tenant objects.

## Login, lock and PIN recovery

Press Enter to begin authentication, then answer each displayed prompt. Secret inputs
are masked and informational instructions remain visible. Escape cancels and leaves
the screen locked or at the greeter. **Reset work PIN** (`Ctrl+R`) starts the configured
account's recovery flow; it cannot itself unlock or create a desktop.

The locker helper is unprivileged, accepts no alternate user/service argument, and
binds its current UID to the root-managed configured UPN. SDDM repeats the same identity
checks in its privileged helper. Both require successful PAM account authorization
before token change, and reject identity changes during that check. A cancelled
operation cannot undo a credential change already committed by the daemon.

## Security and maintenance

Privileged commands use fixed executable paths and an isolated Python interpreter.
The companion tracks exact managed files, identity mappings and native payload hashes;
unknown configuration, source or package drift is refused. Explicit `repair` migrates
recognized older state. See [security policy](SECURITY.md), [maintenance](docs/maintenance.md)
and [release trust boundaries](docs/releasing.md).

A user-owned lock clone is a snapshot: review it after Omarchy updates. Quickshell's
normal unlock interface authenticates without PAM account management; the session
already belongs to that logged-in user. PIN recovery separately enforces account policy.
NSS remains a trusted identity authority, and shared kernel UIDs cannot be made
independent security principals by this integration.

Before root rollback, disable the clone and exit every session loading it. The command
refuses live or indeterminate graphical sessions by default. From independent recovery
access, `recover` restores only known interrupted transaction states. Package removal
alone does not reverse setup, PIN changes, tenant objects or Himmelblau's package hooks.

Intune policy processing is an explicit opt-in (`policy --enable`); tenant scripts can
run with root privileges. Enrollment does not prove compliance. The browser extension's
Online and compliance badges do not establish successful web SSO. All-site Background
SSO is a deliberate broader permission and still requires browser approval; read
[the current extension limitation](docs/intune-sso.md#background-sso-permissions).

## Development

```sh
make test
python3 tools/prepare-native.py /tmp/omarchy-native-source
```

CI compiles the actual patched Qt6 daemon/helper and Qt5 greeter and runs native security
and lifecycle proofs. `bin/omarchy-himmelblau --development` explicitly runs checkout
code as an unprivileged user; root checkout execution through that launcher is refused.
Use a disposable VM for privileged integration and actual authentication acceptance.

Companion code and Omarchy adaptations use [MIT](LICENSE). SDDM changes retain their
upstream GPL/LGPL licenses; see [third-party notices](THIRD-PARTY-NOTICES.md).
