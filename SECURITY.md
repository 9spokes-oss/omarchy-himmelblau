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

See [maintenance](docs/maintenance.md) for identity trust, rollback and recovery limits,
and [release verification](docs/releasing.md) for artifact provenance and its limits.
