# System-wide build/install, used by the distribution packages
# (packaging/) and the Release workflow. For a per-user install use ./install.sh.
#
#   make                                   build the NVKMS helper
#   make install DESTDIR=/tmp/root PREFIX=/usr

APP     := linuxnvidiahwcc
PREFIX  ?= /usr
DESTDIR ?=

BINDIR  := $(PREFIX)/bin
LIBDIR  := $(PREFIX)/lib/$(APP)
APPSDIR := $(PREFIX)/share/applications
ICONDIR := $(PREFIX)/share/icons/hicolor/scalable/apps

all: helper

helper:
	$(MAKE) -C helper

install: helper
	install -d $(DESTDIR)$(LIBDIR)/$(APP)/icons $(DESTDIR)$(BINDIR) \
		$(DESTDIR)$(APPSDIR) $(DESTDIR)$(ICONDIR)
	install -m 644 $(APP)/*.py $(DESTDIR)$(LIBDIR)/$(APP)/
	install -m 644 $(APP)/icons/$(APP).svg $(DESTDIR)$(LIBDIR)/$(APP)/icons/
	install -m 755 helper/nvkms-vibrance $(DESTDIR)$(LIBDIR)/$(APP)/nvkms-vibrance
	printf '#!/bin/sh\nexec env PYTHONSAFEPATH=1 PYTHONPATH="%s" python3 -m %s "$$@"\n' \
		'$(LIBDIR)' '$(APP)' > $(DESTDIR)$(BINDIR)/$(APP)
	chmod 755 $(DESTDIR)$(BINDIR)/$(APP)
	install -m 644 packaging/$(APP).desktop $(DESTDIR)$(APPSDIR)/$(APP).desktop
	install -m 644 $(APP)/icons/$(APP).svg $(DESTDIR)$(ICONDIR)/$(APP).svg

clean:
	$(MAKE) -C helper clean
	rm -rf dist

.PHONY: all helper install clean
