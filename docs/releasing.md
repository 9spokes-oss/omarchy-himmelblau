# Building and verifying a release

The recipes in `packaging/` are templates and cannot build from an arbitrary
checkout. Release from a clean, reviewed commit: the exporter ships only `git
archive` content and records the commit, tree and archive hash. Never run
`makepkg` as root.

Never build a release with `makepkg --nocheck`. The native package's `check()`
runs the security proofs; skipping it skips them.

```sh
make test
python3 tools/prepare-release.py /path/outside-checkout/release
cd /path/outside-checkout/release/native
makepkg --noconfirm
```

The native build verifies the pinned SDDM archive and patches, builds Qt6 and Qt5,
and runs the proofs. Inspect the result independently, then pin it into the
companion package:

```sh
python3 /path/to/checkout/tools/finish-release.py /path/outside-checkout/release \
  /path/outside-checkout/release/native/sddm-himmelblau-[0-9]*.pkg.tar.zst
cd /path/outside-checkout/release/companion
makepkg --noconfirm
```

`finish-release.py` reads only metadata and hashes from the native archive; it
installs nothing. The companion manifest pins the six native authentication
executables and the packaged QML components. It refuses a release directory that
is already pinned: export a fresh one to pin a different package.

Install build dependencies in a disposable Arch environment. If the companion
cannot find the new native package as a dependency, `makepkg --nodeps` is fine
only after you check every other dependency and confirm the exact native package
built. It does not waive the runtime dependency. Installing a package starts no
services and configures no PAM.

Then, from a checkout at the release commit:

```sh
python3 tools/corresponding-source.py /path/outside-checkout/release
```

Generate package checksums after the final build. Keep `release.json`, both
package archives, `source.tar.gz`, `sddm-corresponding-source.tar.gz` and
`SHA256SUMS` together. Publish the corresponding source (complete patched native
source) and `source.tar.gz` (companion, patches, recipes, tests) with the native
binary, so a release does not depend on the upstream download staying available.

## Publishing

- Publish from the reviewed commit as an experimental prerelease, listing what was
  validated and the known limitations.
- Before publishing, review all reachable Git history and the release files for
  private data, check third-party licenses and source provenance, and record VM
  acceptance. Release nothing containing private VM files, account, tenant or
  device identifiers, credentials, or authentication traces.
- Users should verify checksums through a trusted channel before `pacman -U`.
  Without a separate signature, checksums catch mismatches but not a compromise of
  the channel that serves them.
- Builds are not bit-for-bit reproducible. CI compiles the native code and tests
  the protocol, but it does not replace real tenant authentication or desktop
  acceptance.
- An upstream PR or marketplace submission stays pending until its maintainers
  accept it.
