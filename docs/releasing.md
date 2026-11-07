# Building and verifying a release

The recipes in `packaging/` are release templates. They deliberately cannot build
directly from an arbitrary checkout. Use a clean, reviewed commit; the exporter
includes only `git archive` content and records its commit, tree and archive hash.
Never run `makepkg` as root.

```sh
make test
python3 tools/prepare-release.py /path/outside-checkout/release
cd /path/outside-checkout/release/native
makepkg --noconfirm
```

The native recipe verifies the pinned SDDM archive and patches, builds Qt6 and Qt5,
and executes the native security proofs. Its proof sources come from the same
checksum-pinned companion source archive. After independently inspecting that build:

```sh
python3 /path/to/checkout/tools/finish-release.py /path/outside-checkout/release \
  /path/outside-checkout/release/native/sddm-himmelblau-0.21.0-3-x86_64.pkg.tar.zst
cd /path/outside-checkout/release/companion
makepkg --noconfirm
```

Install build dependencies from the recipes in a disposable Arch build environment.
If the new native dependency is not installed there, `makepkg --nodeps` for the
companion is acceptable only after separately checking that every other dependency
is present and the exact native package has built successfully. It is not a runtime
dependency waiver. Package installation does not start services or configure PAM.

`finish-release.py` extracts only metadata and hashes from the exact native archive;
it does not install it. The resulting companion payload manifest pins all six native
authentication executables and packaged QML components. Keep `release.json`, both
package archives, `source.tar.gz`, and SHA256SUMS together. Generate package checksums
after the final build. From the exact clean release checkout, also run
`python3 tools/corresponding-source.py /path/outside-checkout/release`. Publish its
`sddm-corresponding-source.tar.gz` (complete patched native source) together with
`source.tar.gz` (companion, patches, recipes and tests) alongside the native binary;
do not rely solely on an upstream source download remaining available.

Publish them from the reviewed commit as an **experimental
prerelease**, with actual validation and known limitations. Do not publish private
VM files, account/tenant/device identifiers, credentials, or authentication traces.

Verify release checksums through a trusted publisher/source channel before `pacman -U`.
Checksums detect mismatches; without a separately trusted signature they do not prove
publisher identity against compromise of that same distribution channel. These builds
are not asserted bit-for-bit reproducible. CI compiles actual native code and tests
the protocol but cannot substitute real tenant authentication or desktop acceptance.

Before publication, review all reachable Git history and release payloads for private
data, check third-party licenses and exact source provenance, and record VM acceptance.
An upstream PR or marketplace submission remains pending until its maintainers accept it.
