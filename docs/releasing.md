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

## AUR

`packaging/aur/` holds AUR recipes for both packages. They build from the tagged
GitHub release archive instead of a local export.

- **The native pin.** A local build has no published native artifact, so the AUR
  companion pins the files of the *installed* `sddm-himmelblau` when it is built
  (`tools/pin-installed-native.py`), taking the list from `pacman -Qlq`, so a packaged
  file that is missing on the builder, or no longer a regular file, fails the build. The manifest is marked `"build": "local"`.
  `doctor` still detects later changes to those files. That they came from the
  reviewed source rests on the recipes' pinned archive checksums, plus the
  companion's check that the tag still points at the reviewed commit.
- **One source of truth.** The native recipe takes its patches, PAM service and
  proofs from the release archive, so the archive checksum covers them.
  `tests/test_aur.py` checks that versions, dependencies and `build()` match the
  release recipe.
- **No `omarchy` dependency.** The `omarchy` package is only in Omarchy's own
  repository, so an AUR package can't depend on it. `setup` refuses to run without
  Omarchy's lock source instead. This is the only intended difference from the
  release recipe's dependencies.
- **Himmelblau comes first.** Both recipes need `himmelblau>=4.0.8`. Until the AUR's
  `himmelblau` package carries 4.0.8 or later, the recipes can't resolve their
  dependencies. Update that package through its maintainer, or adopt it if it is
  orphaned, rather than uploading a duplicate.

To publish after tagging `vX.Y.Z` and pushing the tag:

1. In both recipes, replace `@RELEASE_SHA256@` with the archive checksum
   (`updpkgsums`) and `@MAINTAINER@` with the AUR account holder's
   `Name <address at domain dot tld>`. In the companion, replace `@RELEASE_COMMIT@`
   with the tag's full commit hash.
2. Build both in a clean Arch environment: native first (its `check()` runs the
   proofs), install it, then build the companion. Never use `--nocheck`.
3. Generate `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO` and push each
   recipe with its `LICENSE` (0BSD, covering the recipe only) to its AUR
   repository (`ssh://aur@aur.archlinux.org/<pkgname>.git`).
   Don't commit filled-in checksums or `.SRCINFO` back to `packaging/aur/`; the
   templates keep the placeholders.

Any change to the native package's `pkgrel` changes `NATIVE_VERSION`, so
AUR-only fixes still need a companion release.
