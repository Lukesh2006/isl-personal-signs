"""Command line: python -m personal_signs <command> (or `personal-signs <command>`)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-signs", description="Personal ISL sign recognition")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the web API and browser demo")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--proxy-headers", action="store_true", help="trust X-Forwarded-For from a local proxy/tunnel")

    commands.add_parser("download-models", help="download the MediaPipe model files")

    add = commands.add_parser("add", help="add an example clip for a sign")
    add.add_argument("label")
    add.add_argument("video", type=Path)
    add.add_argument("--user", default="default")
    add.add_argument("--kind", choices=["isl_variant", "custom_gesture"])
    add.add_argument("--confirm", action="store_true", help="save even if the gate warns")

    for name, text in (("train", "build the personal model"), ("list", "list saved signs")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--user", default="default")

    recognize = commands.add_parser("recognize", help="recognize one clip")
    recognize.add_argument("video", type=Path)
    recognize.add_argument("--user", default="default")

    tune = commands.add_parser("tune", help="measure the match cut-offs across signers")
    tune.add_argument("--max-false-match", type=float, default=0.02)
    tune.add_argument("--exact", action="store_true", help="re-read 8-frame queries from video (slower)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "personal_signs.api:app",
            host=args.host,
            port=args.port,
            proxy_headers=args.proxy_headers,
            forwarded_allow_ips="127.0.0.1" if args.proxy_headers else None,
        )
        return 0

    if args.command == "download-models":
        from .landmarks import download_models

        for path in download_models():
            print(path)
        return 0

    from . import engine

    try:
        if args.command == "add":
            _print(engine.save_sample(args.user, args.label, args.video.read_bytes(), args.kind, args.confirm))
        elif args.command == "train":
            _print(engine.train_user(args.user) if engine.list_samples(args.user) else engine.train_seed_model())
        elif args.command == "list":
            _print(engine.list_sample_details(args.user))
        elif args.command == "recognize":
            _print(engine.predict_user(args.user, args.video.read_bytes()))
        elif args.command == "tune":
            from . import tuning

            tuning.print_report(tuning.report(args.max_false_match, args.exact))
    except (ValueError, LookupError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.shutdown_extractors()
    return 0


if __name__ == "__main__":
    sys.exit(main())
