# Security policy

This is experimental authentication software, tested against a narrow Omarchy,
Himmelblau and SDDM combination. Keep an independent local recovery account. It is
not an official Omarchy, Himmelblau, Microsoft or SDDM distribution.

Report exploitable vulnerabilities through this repository's private vulnerability
reporting facility when available. Do not include passwords, PINs, tokens, PRTs,
tenant/device identifiers or authentication transcripts in issues. If private
reporting is unavailable, request a private contact without publishing exploit or
credential details. Ordinary reproducible bugs can use GitHub issues.

Security changes require focused regression checks and review of the actual native
authentication code. A theme, browser Online badge, passing package name check or
Intune enrollment alone does not establish authorization, SSO, or compliance.

PIN recovery performs `pam_acct_mgmt` before `pam_chauthtok`. Account management
checks account policy; it does not authenticate the caller. In Himmelblau 4.0.8,
the [normal PAM reset path](https://github.com/himmelblau-idm/himmelblau/blob/a969c7488164dc67e6159c9ecc5eaa6329eb7664/src/pam/src/pam/mod.rs#L695-L779)
starts fresh online Entra MFA; password requirements depend on passwordless/server
policy. This is source evidence, not live acceptance of the new integration.

The [daemon token-change API](https://github.com/himmelblau-idm/himmelblau/blob/a969c7488164dc67e6159c9ecc5eaa6329eb7664/src/daemon/src/daemon.rs#L927-L975)
authorizes root or matching peer UID, and uses a caller-supplied bearer token
[without signature verification](https://github.com/himmelblau-idm/himmelblau/blob/a969c7488164dc67e6159c9ecc5eaa6329eb7664/src/daemon/src/daemon.rs#L250-L265).
The normal UI's MFA flow therefore does not establish cryptographic MFA enforcement
at that API against another process already running as the same Unix UID.

The optional M365 policy generator runs as root only from its fixed installed path,
using isolated Python and a root-owned, non-writable installed preset and ancestry.
Unprivileged checkout use is limited to generating a preview.

See [maintenance](docs/maintenance.md) for identity trust, rollback and recovery limits,
and [release verification](docs/releasing.md) for artifact provenance and its limits.
