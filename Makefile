PREFIX ?= /usr
DESTDIR ?=

.PHONY: test install

CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror

build/pin-change: src/pin-change.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< -lpam


test:
	python3 -m unittest discover -s tests -v
	node tests/pam-conversation.mjs

install: build/pin-change
	install -Dm755 build/pin-change $(DESTDIR)$(PREFIX)/lib/omarchy-himmelblau/pin-change
	install -Dm755 bin/omarchy-himmelblau $(DESTDIR)$(PREFIX)/bin/omarchy-himmelblau
	install -Dm644 src/omarchy_himmelblau.py $(DESTDIR)$(PREFIX)/lib/omarchy-himmelblau/omarchy_himmelblau.py
	install -dm755 $(DESTDIR)$(PREFIX)/share/omarchy-himmelblau
	install -m644 assets/*.patch assets/manifest.json $(DESTDIR)$(PREFIX)/share/omarchy-himmelblau/
	install -Dm644 LICENSE $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/LICENSE
	install -Dm644 THIRD-PARTY-NOTICES.md $(DESTDIR)$(PREFIX)/share/licenses/omarchy-himmelblau/THIRD-PARTY-NOTICES.md
	install -Dm644 README.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/README.md
	install -Dm644 docs/maintenance.md $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/maintenance.md
	install -Dm644 assets/menu-extension.jsonc $(DESTDIR)$(PREFIX)/share/doc/omarchy-himmelblau/menu-extension.jsonc
