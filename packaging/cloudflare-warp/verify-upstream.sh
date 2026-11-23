#!/bin/sh
# Check the .deb named by PKGBUILD against Cloudflare's signed apt metadata:
#   pinned repo key -> InRelease signature -> Packages SHA-256 -> .deb SHA-256
# and compare with the PKGBUILD's sha256sums. Run when bumping pkgver.
# Needs curl, gpg (gpgv) and awk. Downloads only metadata, not the .deb.
set -eu

KEY_FPR=C068A2B5771775193CBE1F2F6E2DD2174FA1C3BA
REPO=https://pkg.cloudflareclient.com

here=$(dirname "$(realpath "$0")")
pkgver=$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")
dist=$(sed -n 's/^_dist=//p' "$here/PKGBUILD")
pinned=$(sed -n "s/^sha256sums=('\([0-9a-f]*\)')$/\1/p" "$here/PKGBUILD")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

curl -fsSL "$REPO/pubkey.gpg" -o "$work/key.asc"
gpg --batch --quiet --homedir "$work" --import "$work/key.asc" 2>/dev/null
gpg --batch --homedir "$work" --with-colons --fingerprint | grep -q "^fpr:::::::::$KEY_FPR:" \
  || { echo "repo key fingerprint is not $KEY_FPR" >&2; exit 1; }
gpg --batch --homedir "$work" --export "$KEY_FPR" >"$work/key.gpg"

curl -fsSL "$REPO/dists/$dist/InRelease" -o "$work/InRelease"
gpgv --keyring "$work/key.gpg" --output "$work/Release" "$work/InRelease" 2>"$work/gpgv.log" \
  || { cat "$work/gpgv.log" >&2; echo "InRelease signature check failed" >&2; exit 1; }

curl -fsSL "$REPO/dists/$dist/main/binary-amd64/Packages" -o "$work/Packages"
packages_sha=$(sha256sum "$work/Packages" | cut -d' ' -f1)
awk '/^SHA256:/{s=1;next} /^[^ ]/{s=0} s{print $1, $3}' "$work/Release" \
  | grep -qx "$packages_sha main/binary-amd64/Packages" \
  || { echo "Packages is not the one InRelease signs" >&2; exit 1; }

deb_sha=$(awk -v v="$pkgver" '
  /^Package: /{p=($2=="cloudflare-warp")} /^Version: /{ver=$2}
  /^SHA256: /{if (p && ver==v) {print $2; exit}}' "$work/Packages")
[ -n "$deb_sha" ] || { echo "cloudflare-warp $pkgver is not in $dist" >&2; exit 1; }

echo "signed chain: cloudflare-warp $pkgver ($dist) sha256 $deb_sha"
if [ "$deb_sha" = "$pinned" ]; then
  echo "PKGBUILD pin matches"
else
  echo "PKGBUILD pin $pinned does NOT match; set sha256sums=('$deb_sha')" >&2
  exit 1
fi
