#!/bin/sh
# Build the PAM stack harness image and run its tests. Needs podman.
# Set PAM_STACK_FAST=1 to skip the test that waits out the 120-second lockout.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
ca=${PAM_STACK_CA:-/etc/ssl/certs/ca-certificates.crt}
mount=
[ -r "$ca" ] && mount="-v $ca:/hostca.pem:ro"
# shellcheck disable=SC2086
podman build $mount -t omarchy-himmelblau-pam-stack "$here"
podman run --rm -e PAM_STACK_FAST="${PAM_STACK_FAST:-}" omarchy-himmelblau-pam-stack "$@"
# Known-bad stacks must each fail the tests aimed at them.
if [ $# -eq 0 ]; then
    podman run --rm -e PAM_STACK_FAST=1 omarchy-himmelblau-pam-stack python3 /harness/test_stack.py --mutants
fi
