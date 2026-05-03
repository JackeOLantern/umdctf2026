#!/usr/bin/env python3
"""
Solver for UMDCTF rev/roulette.

The binary expects a 106-byte line. The accepted line is the flag itself.
Usage:
  python3 solve_roulette.py
  python3 solve_roulette.py --run ./roulette
"""
import argparse
import subprocess

FLAG = "UMDCTF{I_R3ALLY-want-to-pl4y-the-p0werball,+but-my-d4d-said-no-so-im-b3tting-ill-win-on-POLYMARKETinstead}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", metavar="BIN", help="optional local binary to test the recovered input")
    args = parser.parse_args()

    payload = FLAG.encode("ascii")
    print(FLAG)

    if args.run:
        p = subprocess.run([args.run], input=payload, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(p.stdout.decode(errors="replace"), end="")


if __name__ == "__main__":
    main()
