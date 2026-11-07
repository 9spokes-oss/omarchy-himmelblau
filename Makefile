PREFIX ?= /usr
DESTDIR ?=

.PHONY: test native-test install

CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror

build/pin-change: src/pin-change.c src/recovery-account.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< -lpam


test:
	python3 -m unittest discover -s tests -v
	node tests/pam-conversation.mjs
	node tests/sddm-conversation.mjs

native-test:
	test -n "$(SDDM_SOURCE)" -a -n "$(SDDM_BUILD)"
	python3 tests/native-sddm/run.py --source "$(SDDM_SOURCE)" --sddm-build "$(SDDM_BUILD)"

install: build/pin-change
	install -dm755 $(DESTDIR)/etc/omarchy-himmelblau
	install -Dm755 build/pin-change $(DESTDIR)$(PREFIX)/lib/omarchy-himmelblau/pin-change
	install -Dm755 bin/omarchy-himmelblau $(DESTDIR)$(PREFIX)/bin/omarchy-himmelblau
	install -Dm644 src/omarchy_himmelblau.py $(DESTDIR)$(PREFIX)/lib/omarchy-himmelblau/omarchy_himmelblau.py
	install -dm755 $(DESTDIR)$(PREFIX)/share/omarchy-himmelblau
	install -m644 assets/*.patch assets/manifest.json $(DESTDIR)$(PREFIX)/share/omarchy-himmelblau/
	install -Dm644 LICENSE $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/LICENSE
	install -Dm644 THIRD-PARTY-NOTICES.md $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/THIRD-PARTY-NOTICES.md
	install -dm755 $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/LICENSES
	install -m644 LICENSES/*.txt $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/LICENSES/
	install -Dm644 README.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/README.md
	install -Dm644 docs/maintenance.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/maintenance.md
	install -Dm644 docs/sddm.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/sddm.md
	install -Dm644 docs/intune-sso.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/intune-sso.md
	install -Dm644 docs/releasing.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/releasing.md
	install -Dm755 examples/sso-test $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/examples/sso-test
	install -Dm644 assets/menu-extension.jsonc $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/menu-extension.jsonc
