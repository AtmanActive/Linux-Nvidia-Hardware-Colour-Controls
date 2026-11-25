"""Command line entry point.

  linuxnvidiahwcc            start in the system tray (window hidden)
  linuxnvidiahwcc --show     start (or bring up the running instance) with the window open
  linuxnvidiahwcc --apply    apply saved settings once, no GUI
  linuxnvidiahwcc --reset    restore neutral settings on all displays, no GUI
  linuxnvidiahwcc --status   print where each adjustment is applied, no GUI
"""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__


def _headless(action: str) -> int:
    from . import config, engine

    cfg = config.load()
    eng = engine.Engine(cfg)
    displays = eng.refresh()
    for w in eng.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if action == "reset":
        for d in displays:
            cfg.set_output(d.name, config.OutputSettings())
        config.save(cfg)
    errors = [] if action == "status" else eng.apply_all()
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if action != "status":
        time.sleep(0.4)
        for d in displays:
            eng.ensure_hardware(d.name)
    for d in displays:
        st = eng.verify(d.name)
        s = cfg.output(d.name)
        print(f"{d.label}: brightness {s.brightness}% contrast {s.contrast}% gamma {s.gamma:.2f} "
              f"vibrance {s.vibrance}%")
        print(f"  brightness/contrast/gamma [{st.tone.level}] {st.tone.text}")
        print(f"  digital vibrance          [{st.vibrance.level}] {st.vibrance.text}")
    return 1 if errors else 0


def main() -> int:
    p = argparse.ArgumentParser(prog="linuxnvidiahwcc", description="Linux Nvidia Hardware Colour Controls (KDE Plasma)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--show", action="store_true", help="open the window instead of starting minimised")
    g.add_argument("--apply", action="store_true", help="apply saved settings and exit")
    g.add_argument("--reset", action="store_true", help="reset all displays to neutral and exit")
    g.add_argument("--status", action="store_true", help="print hardware status and exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args()

    if a.apply or a.reset or a.status:
        return _headless("apply" if a.apply else "reset" if a.reset else "status")

    from .app import run_gui
    return run_gui(show=a.show)


if __name__ == "__main__":
    sys.exit(main())
