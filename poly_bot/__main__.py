"""Command line: `python -m poly_bot` opens the web app; `run` and `plan` work without it.

    python -m poly_bot                       # web app at http://127.0.0.1:8090
    python -m poly_bot web --port 8091 --no-browser
    python -m poly_bot run --profile btc-5m  # headless: run one bot until Ctrl+C
    python -m poly_bot plan --profile btc-5m [--price 0.5]   # print the bot's ladder
"""
from __future__ import annotations

import argparse
import sys
import threading


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="poly_bot", description="Martingale bot for Polymarket Up or Down rounds")
    sub = p.add_subparsers(dest="cmd")
    w = sub.add_parser("web", help="open the web app (default)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8090)
    w.add_argument("--no-browser", action="store_true")
    w.add_argument("--public-host", action="append", default=[], metavar="DOMAIN",
                   help="also answer to this domain, e.g. poly.zodypay.com (only behind a password-protected "
                        "HTTPS proxy such as Caddy with basic_auth)")
    hs = sub.add_parser("hosted", help="the multi-user app with email sign-in (behind an HTTPS proxy)")
    hs.add_argument("--host", default="127.0.0.1")
    hs.add_argument("--port", type=int, default=8090)
    hs.add_argument("--adopt", default="", metavar="EMAIL",
                    help="move the bots in profiles/ into this user's account (once, when switching to hosted)")
    r = sub.add_parser("run", help="run one bot without the web app")
    r.add_argument("--profile", required=True, help="bot id: the folder name in profiles/")
    pl = sub.add_parser("plan", help="print a bot's ladder")
    pl.add_argument("--profile", required=True)
    pl.add_argument("--price", type=float, default=0.5, help="entry price per share (default 0.5)")
    args = p.parse_args(argv)

    from .web import setup_logging
    setup_logging()
    if args.cmd == "hosted":
        from .hosted import load_env, serve_hosted
        load_env()
        serve_hosted(args.host, args.port, args.adopt)
        return 0
    if args.cmd in (None, "web"):
        from .web import serve
        serve(getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8090), not getattr(args, "no_browser", False),
              tuple(getattr(args, "public_host", []) or ()))
        return 0

    from .instance import PROFILES_DIR
    from .web import Controller
    folder = PROFILES_DIR / args.profile
    if not (folder / "config.json").exists():
        print(f"No bot {args.profile!r}: make it in the web app first (profiles/<id>/config.json)", file=sys.stderr)
        return 1
    ctl = Controller(args.profile, folder)
    if args.cmd == "plan":
        view = ctl.plan({"price": args.price})
        print(f"{ctl.cfg.display_name}: {ctl.cfg.sizing} sizing at price {view['price']:.2f}, "
              f"budget ${view['budget']:.2f} -> {view['fits']} step(s) fit")
        print(f"{'step':>4} {'shares':>9} {'cost':>10} {'win earns':>10} {'lost if lose':>13}")
        for row in view["rows"]:
            print(f"{row['step']:>4} {row['shares']:>9.2f} {row['cost']:>10.2f} {row['win_profit']:>10.2f} "
                  f"{row['lost_total']:>13.2f}{'' if row['fits'] else '   <- over budget'}")
        return 0
    ctl.start()
    try:
        while ctl.running:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    ctl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
