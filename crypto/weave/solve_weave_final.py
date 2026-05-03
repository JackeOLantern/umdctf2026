#!/usr/bin/env python3
"""
UMDCTF crypto/weave solver.

Usage:
  python3 solve_weave.py output.json
  python3 solve_weave.py output.json -v

Dependencies:
  - pycryptodome:  pip install pycryptodome
    or cryptography: pip install cryptography
"""

import argparse
import hashlib
import json


class GF2m:
    def __init__(self, m: int, modulus: int):
        self.m = m
        self.modulus = modulus
        self.mask = (1 << m) - 1

    def mul(self, a: int, b: int) -> int:
        out = 0
        while b:
            if b & 1:
                out ^= a
            b >>= 1
            a <<= 1
            if a & (1 << self.m):
                a ^= self.modulus
        return out & self.mask

    def pow(self, a: int, e: int) -> int:
        out = 1
        while e:
            if e & 1:
                out = self.mul(out, a)
            a = self.mul(a, a)
            e >>= 1
        return out

    def inv(self, a: int) -> int:
        if a == 0:
            raise ZeroDivisionError("zero has no inverse")
        return self.pow(a, (1 << self.m) - 2)

    def qpow(self, a: int, j: int) -> int:
        # Frobenius power: a^(2^j)
        j %= self.m
        for _ in range(j):
            a = self.mul(a, a)
        return a

    def qroot(self, a: int, j: int) -> int:
        # Inverse of a -> a^(2^j) in GF(2^m)
        return self.qpow(a, -j)


def modulus_from_coefficients(coeffs: list[int]) -> int:
    value = 0
    for i, c in enumerate(coeffs):
        if c:
            value |= 1 << i
    return value


def row_times_matrix(row: list[int], matrix: list[list[int]], field: GF2m) -> list[int]:
    cols = len(matrix[0])
    out = [0] * cols
    for i, value in enumerate(row):
        if value:
            for j in range(cols):
                out[j] ^= field.mul(value, matrix[i][j])
    return out


def invert_matrix(matrix: list[list[int]], field: GF2m) -> list[list[int]]:
    n = len(matrix)
    work = [
        matrix[i][:] + [1 if i == j else 0 for j in range(n)]
        for i in range(n)
    ]

    for col in range(n):
        pivot = next((r for r in range(col, n) if work[r][col]), None)
        if pivot is None:
            raise ValueError("singular matrix")

        work[col], work[pivot] = work[pivot], work[col]
        inv_pivot = field.inv(work[col][col])
        work[col] = [field.mul(x, inv_pivot) for x in work[col]]

        for r in range(n):
            if r != col and work[r][col]:
                factor = work[r][col]
                work[r] = [
                    work[r][j] ^ field.mul(factor, work[col][j])
                    for j in range(2 * n)
                ]

    return [row[n:] for row in work]


def solve_linear_system_fqm(
    matrix: list[list[int]],
    rhs: list[int],
    field: GF2m,
) -> tuple[list[int], int]:
    rows = len(matrix)
    cols = len(matrix[0])
    work = [matrix[i][:] + [rhs[i]] for i in range(rows)]
    pivots = []
    rank = 0

    for col in range(cols):
        pivot = next((r for r in range(rank, rows) if work[r][col]), None)
        if pivot is None:
            continue

        work[rank], work[pivot] = work[pivot], work[rank]
        inv_pivot = field.inv(work[rank][col])
        work[rank] = [field.mul(x, inv_pivot) for x in work[rank]]

        for r in range(rows):
            if r != rank and work[r][col]:
                factor = work[r][col]
                work[r] = [
                    work[r][j] ^ field.mul(factor, work[rank][j])
                    for j in range(cols + 1)
                ]

        pivots.append(col)
        rank += 1

    for r in range(rank, rows):
        if all(work[r][c] == 0 for c in range(cols)) and work[r][cols]:
            raise ValueError("inconsistent system")

    solution = [0] * cols
    for r, col in enumerate(pivots):
        solution[col] = work[r][cols]

    return solution, rank


def decode_gabidulin(
    received: list[int],
    pegs: list[int],
    k: int,
    error_rank_bound: int,
    field: GF2m,
    verbose: bool = False,
) -> list[int]:
    """
    Decodes y_i = f(pegs_i) + e_i with rank(e) <= error_rank_bound.

    It solves the linearized key equation:
      N(g_i) = Lambda(y_i)
    with deg_q(N) < k+t and monic deg_q(Lambda)=t.
    """
    t = error_rank_bound
    n = len(pegs)
    n_unknowns = k + t
    cols = n_unknowns + t

    matrix = []
    rhs = []
    for i in range(n):
        row = [field.qpow(pegs[i], j) for j in range(n_unknowns)]
        row += [field.qpow(received[i], j) for j in range(t)]
        matrix.append(row)
        rhs.append(field.qpow(received[i], t))

    solution, rank = solve_linear_system_fqm(matrix, rhs, field)
    if verbose:
        print(f"info: key-equation rank = {rank}/{cols}")

    N = solution[:n_unknowns]
    Lambda = solution[n_unknowns:] + [1]  # monic term

    # Right division of linearized polynomials: N = Lambda o f.
    f = [0] * k
    for degree in range(k + t - 1, t - 1, -1):
        acc = N[degree]
        for a in range(t):
            b = degree - a
            if 0 <= b < k and f[b]:
                acc ^= field.mul(Lambda[a], field.qpow(f[b], a))
        f[degree - t] = field.qroot(acc, t)

    return f


def aes_gcm_decrypt(key: bytes, iv: bytes, body: bytes, tag: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        return cipher.decrypt_and_verify(body, tag)
    except ModuleNotFoundError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(iv, body + tag, None)


def derive_key(secret: list[int], m: int) -> bytes:
    element_size = (m + 7) // 8
    secret_bytes = b"".join(x.to_bytes(element_size, "big") for x in secret)
    return hashlib.sha256(secret_bytes).digest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_json", nargs="?", default="output.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    handout = json.load(open(args.output_json, "r", encoding="utf-8"))
    spec = handout["spec"]

    m = spec["m"]
    k = spec["k"]
    frays = spec["frays"]
    field = GF2m(m, modulus_from_coefficients(spec["modulus"]))

    bolt = handout["bolt"]
    loom = handout["loom"]
    pegs = loom["pegs"]
    knot = loom["knot"]
    shuttle = loom["shuttle"]
    fibers = loom["fibers"]

    # Undo the public shuttle mask:
    #   bolt * shuttle = (secret * knot) * loom + frays * shuttle
    # The error rank is at most frays * len(fibers) = 15.
    received = row_times_matrix(bolt, shuttle, field)
    error_rank_bound = frays * len(fibers)

    if args.verbose:
        print(f"info: GF(2^{m}), k={k}, error-rank-bound={error_rank_bound}")

    secret_times_knot = decode_gabidulin(
        received,
        pegs,
        k,
        error_rank_bound,
        field,
        verbose=args.verbose,
    )

    secret = row_times_matrix(secret_times_knot, invert_matrix(knot, field), field)
    key = derive_key(secret, m)

    vault = handout["vault"]
    flag = aes_gcm_decrypt(
        key,
        bytes.fromhex(vault["iv"]),
        bytes.fromhex(vault["body"]),
        bytes.fromhex(vault["tag"]),
    )

    if args.verbose:
        print("info: AES-GCM tag verified")
        print("info: secret =", [hex(x) for x in secret])
        print("info: key =", key.hex())

    print(flag.decode())


if __name__ == "__main__":
    main()
