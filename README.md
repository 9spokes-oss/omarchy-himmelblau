# Omarchy Himmelblau integration

Experimental integration for signing in to Omarchy with Microsoft Entra ID through [Himmelblau](https://himmelblau-idm.org/). It combines system authentication setup with Omarchy's SDDM login and a user-scoped lock-screen clone. Keep a working local recovery account and an independent console before changing authentication.

## Status

A manual proof of concept on a QEMU/KVM guest tested Omarchy `4.5.0-3`, Himmelblau `4.0.8-1` and Quickshell `0.3.2.r7.g2b4501a-1`. Entra login, Hello PIN unlock, authenticated sudo, Omarchy updates, and local recovery were exercised. That test used `apply_policy = false` and a software HSM. It did not establish Intune enrollment or compliance, offline authentication, hardware-backed key protection, or compatibility with other versions and tenant policies.

The `0.1.0-3` package has been built and installed in that guest. System setup and checks passed; the packaged greeter accepted a local recovery login, and the stock locker accepted the local password. A work user signed in and the native lock clone was created and verified as the enabled third-party clone. After a fresh login, the user confirmed that authenticated sudo and PIN unlock both worked. An independent check observed the work Wayland session, an unlocked Quickshell state, the clone's enabled provenance, and passing user and root status checks. The source checks and CI passed 23 Python tests and the JavaScript regression.

Version `0.2.0-1` adds interactive lock-screen PAM conversations and an unprivileged PIN-change helper. It was built and installed in the same guest without ending the graphical session or its existing terminal. Thirty Python tests and the conversation regression passed independently. Real on-screen authentication and PIN-change cancellation both left the desktop securely locked; a real-libpam abort at the first PIN prompt also passed. The user completed the prompted PIN-recovery flow, confirmed that it stayed locked after reporting success, and then unlocked the same desktop with the new PIN. Independent metadata checks observed recovery success while securely locked, followed by successful unlock, with the original session, compositor and terminal process IDs and start times unchanged. Normal idle locking was restored. This validates the exercised account/tenant flow, not every possible MFA method or policy.

Version `0.3.0-1` adds a coordinated native SDDM build and conversation theme for login and work-PIN recovery. Independent Qt6/Qt5 builds, 38 Python tests, both JavaScript conversation suites and two native test executables passed. VM acceptance covered local recovery login, current work-PIN login, and cancellation of login and PIN recovery. The user completed the SDDM PIN-recovery/MFA flow; metadata confirmed that success left the same greeter running without a work desktop. A separate login with the new PIN and subsequent screen unlock both worked. Root/user checks passed, and the account's identity, home mapping and existing authentication policy were unchanged. This validates the exercised account/tenant flow. See [SDDM integration and validation](docs/sddm.md).

The integration package itself does not join a device or configure authentication on installation. Installing its Himmelblau dependency can change local authentication configuration, as described below. Setup, login and optional user-level activation are separate actions. The first successful Himmelblau authentication can create an Entra device object. Review tenant device-registration permissions and Conditional Access before testing, and record the object for later lifecycle management.

Version `0.3.0` requires `sddm-himmelblau=0.21.0-1`. This separate package replaces stock SDDM binaries and includes the fixed PIN-change PAM service. It is a reviewed release backport, not an upstream SDDM release or a theme-only extension. The companion selects its copied theme through explicit setup/repair; display-manager restart is an operator action.

## Prerequisites

- An Omarchy installation with a local recovery account and console access.
- The matching native SDDM package for version `0.3.0`, built from `packaging/sddm-himmelblau` with the dependencies declared in that recipe. Keep the previous SDDM and companion packages for coordinated rollback.
- Himmelblau installed separately. Its [upstream Arch build recipe](https://github.com/himmelblau-idm/himmelblau/blob/4.0.8/platform/arch/PKGBUILD) builds from the 4.0.8 source checkout; the upstream package is not distributed here.
- An Entra account and tenant policy that permit the intended authentication and device enrollment flow. Do not change tenant policy just to make a test pass.

Review the Himmelblau package before installing it: its Arch install hook configures PAM and NSS, and the package includes browser managed policies, an SSH configuration drop-in, and a display-manager dependency. Its removal does not automatically remove all PAM/NSS entries. Back up and review the effective system configuration and preserve recovery access.

Himmelblau's main configuration is read by its daemon and PAM module; do not store secrets in it. An existing configuration must be root-owned mode `0644` in a root-owned mode `0755` directory. Setup refuses more restrictive or otherwise unsupported permissions for manual review rather than widening them. Its private rollback state remains restricted.

## Operations

Build the native SDDM and companion packages as an unprivileged user from a reviewed checkout with the required Arch build dependencies installed:

```sh
(cd packaging/sddm-himmelblau && makepkg)
(cd packaging && makepkg --nodeps)
```

The companion's `--nodeps` allows building before the matching native SDDM package is installed; it does not supply missing build prerequisites. Review both resulting packages and install them together in one `pacman` transaction. For a display-manager replacement, use an independent recovery console and the transition/rollback procedure in [the SDDM guide](docs/sddm.md). Installing or upgrading the separate Himmelblau dependency can run its own hooks.

Run the local checks from the checkout with:

```sh
make test
```

Run the system setup with the intended domain and one authorized UPN, substituting your own values:

```sh
sudo omarchy-himmelblau setup --domain example.com --user alice@example.com
```

Setup writes configuration but does not start or restart services. For a fresh deployment, explicitly start both daemons after reviewing the configuration:

```sh
sudo systemctl enable --now himmelblaud himmelblaud-tasks
```

If the services were already active when their configuration changed, apply it with `sudo systemctl restart himmelblaud himmelblaud-tasks`. Starting or restarting services does not itself authenticate a user. Confirm their status and effective configuration before an interactive login.

`--admin` is an explicit opt-in for authenticated administrative access and requires the target identity to resolve through NSS at setup time. Review the proposed sudo policy and its scope before using it. Run `sudo omarchy-himmelblau doctor` or `sudo omarchy-himmelblau status` to inspect root-managed state and effective configuration. The unprivileged forms check the current user's clone, hook and readable source files; they cannot inspect root-only configuration. Use `omarchy-himmelblau enable-user` as the target desktop user to activate the user-scoped lock clone. Run it while the session is unlocked, then use `omarchy-restart-shell` while still unlocked to load the new code. This restarts the shell without intentionally ending Hyprland or its applications. `repair` and `rollback` are privileged operations. Consult the command help and review the effective configuration before applying changes.

Setup and checks refuse unreviewed Himmelblau configuration drop-ins and conflicting managed settings; inspect them manually before proceeding. If user activation stops partway through, the native clone may already be enabled. Keep the session unlocked and inspect and disable the clone before retrying.

Native clone IDs under `omarchy.*` are reserved for built-in plugins. Usernames that would produce such an ID, including `omarchy`, are not supported by `enable-user`; it refuses them before making changes. Use the actual target user's session and do not override its identity to work around this restriction.

Package removal alone does not undo setup. Before root rollback and package uninstall, disable the clone through Omarchy **and exit every session using it**: `keepLoaded` code survives disabling, and removing its dedicated PAM service while it is running can strand the next lock. Run root rollback from an independent recovery console or SSH session before starting another clone session. Root rollback covers root-managed integration changes; the user-scoped clone and hook are separate. Review those user-owned files before removing them. Rollback does not remove Himmelblau, reverse its package hooks, delete an Entra or Intune device, or restore an entire system snapshot. Confirm any external device cleanup separately.

## Lock-screen authentication and PIN recovery

Press Enter to start sign-in, then answer the displayed PAM prompt. Each prompt gets a fresh response; secret prompts are masked, and informational messages such as Authenticator instructions remain visible. Escape cancels the current attempt and leaves the desktop locked. Only successful authentication unlocks it.

PIN recovery uses Himmelblau's password-change operation through a small native helper. It runs as the current user, with a fixed PAM service and no alternate-user argument. It is not setuid and does not need sudo. The dedicated service contains only `password required pam_himmelblau.so`.

Choose **Reset work PIN** to follow the prompts. Himmelblau 4.0.8 requests the replacement PIN and confirmation before its cloud authentication/MFA flow. A successful operation replaces that user's Hello credential and clears associated cached credentials; it does not unlock the screen. Sign in separately using the new PIN. Cancelling cannot undo a change the daemon has already committed; if cancellation occurs near completion, the new PIN may already be in effect.

This provides a PAM conversation interface, not an embedded browser or support guarantee for every tenant policy and authentication method. Complete any external browser/phone steps on the appropriate device. Do not intentionally exhaust incorrect-PIN retries or clear global credentials to test recovery.

To upgrade an unchanged managed `0.1.0` clone, install the new package, keep the desktop unlocked with an independent recovery console, and run:

```sh
sudo omarchy-himmelblau repair
omarchy-himmelblau enable-user --upgrade
omarchy-restart-shell
```

The upgrade checks the entire clone and upstream snapshot, stages the replacement outside the watched plugin directory, and exchanges complete directories atomically. Unknown changes are refused. Avoid idle locking or suspend throughout activation; verify the shell is ready before locking. See [maintenance and validation](docs/maintenance.md).

## Security and maintenance

Quickshell's lock PAM interface performs authentication without the PAM account phase. Himmelblau's account-phase allowlist therefore cannot be assumed to run at screen unlock. A lock session belongs to the already logged-in user; test both local recovery and work PIN unlock after changes. Normal SDDM login retains its separate PAM account and session checks. SDDM PIN-change success requires a separate normal login and cannot itself create a session.

The integration derives its dedicated `/etc/pam.d/omarchy-himmelblau-lock-password` service from a supported stock lock PAM configuration. It leaves `/etc/pam.d/omarchy-lock-password` untouched; the guarded user clone uses the dedicated service. An existing modified stock service is refused for manual review. Checks verify both the stock source and the integration's managed files; repair refuses unrecognized drift.

The Omarchy lock clone is user-owned and does not automatically inherit upstream lock-screen fixes. Review it after updates, including changes to the stock PAM source. During activation, keep the session unlocked and retain an independent recovery route. `keepLoaded` means a plugin rescan alone does not load the replacement; use the supported shell restart while unlocked, or log out and back in. Status checks do not prove which code is running. See [maintenance and validation](docs/maintenance.md).

An optional menu example is installed at `/usr/share/doc/omarchy-himmelblau/menu-extension.jsonc`. Add its entries to the existing user-owned menu JSONC object only after reviewing it; the installer does not modify that file.

The native SDDM package is pinned to the reviewed protocol and source. Review upstream security fixes and rebuild the coordinated packages deliberately; do not assume stock SDDM updates will maintain this private protocol. The theme checks the daemon-negotiated protocol capability and fails closed on an incompatible running daemon.

## Licenses

Companion code and the adapted Omarchy theme are available under [MIT](LICENSE). Native SDDM modifications retain their upstream GPL/LGPL file licenses. See [third-party notices](THIRD-PARTY-NOTICES.md) for attribution and license texts. Himmelblau is separately licensed and distributed.
