# satkas/cli/cli.py
"""Console entry point for the SatKas GUI.

Supports:
  satkas
  satkas --peer <onion>
  MAKER_ENDPOINT=<onion> satkas
"""
import os
import sys

# Before logging.basicConfig and before importing satkas.main / Kivy.
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ["KCFG_KIVY_LOG_LEVEL"] = "error"

import argparse
import logging

logging.basicConfig(level=logging.INFO)


def main():
    # Must run before importing satkas.main — Kivy parses sys.argv at
    # import time and rejects unknown options like --peer.
    parser = argparse.ArgumentParser(prog="satkas")
    parser.add_argument(
        "--peer",
        default=None,
        help="Force a maker onion endpoint (sets MAKER_ENDPOINT)",
    )
    args, remaining = parser.parse_known_args()
    if args.peer:
        os.environ["MAKER_ENDPOINT"] = args.peer
    sys.argv = [sys.argv[0]] + remaining

    from satkas.main import main as satkas_main
    satkas_main()


if __name__ == "__main__":
    main()
