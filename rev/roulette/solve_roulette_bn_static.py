#!/usr/bin/env python3
"""
Binary-Ninja-assisted static solver for UMDCTF rev/roulette.

This version does not need ptrace/GDB. It reconstructs the accepted line
from the 27 little-endian dwords recovered after reversing the validator.
"""
import argparse
import struct
import subprocess

WORDS_HEX = """
43444d55 497b4654 4133525f 2d594c4c 746e6177 2d6f742d 79346c70
6568742d 7730702d 61627265 2b2c6c6c 2d747562 642d796d 732d6434
2d646961 732d6f6e 6d692d6f 7433622d 676e6974 6c6c692d 6e69772d
2d6e6f2d 594c4f50 4b52414d 6e695445 61657473 00007d64
"""


def build_payload() -> bytes:
    words = [int(x, 16) for x in WORDS_HEX.split()]
    payload = b"".join(struct.pack("<I", w) for w in words)
    return payload.rstrip(b"\x00")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", metavar="BIN", help="test payload against a local roulette binary")
    ap.add_argument("--raw", action="store_true", help="print raw payload bytes instead of decoded text")
    args = ap.parse_args()

    payload = build_payload()
    if args.raw:
        import sys
        sys.stdout.buffer.write(payload + b"\n")
    else:
        print(payload.decode("ascii"))

    if args.run:
        p = subprocess.run([args.run], input=payload + b"\n", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(p.stdout.decode(errors="replace"), end="")


if __name__ == "__main__":
    main()
