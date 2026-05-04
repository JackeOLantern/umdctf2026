# Choppediver OA — Write-up
**Author:** AnyMoR Entry JOL  
**Writeup/Figures license:** CC BY 4.0  
**Solver license:** MIT
## Summary

The service is a WebSocket/TLS online assessment. For each of 25 rounds it sends a JSON round header, then a PNG chart. The client must answer `buy`, `sell`, or `hold`.

The intended timing is very strict: the last rounds allow only 5 ms. A normal client cannot receive the PNG, classify it, and send the answer inside that server-side window.

The vulnerability is in the timeout loop. The server checks the deadline only when `ws.recv(timeout=0.001)` times out. If the client keeps sending valid `heartbeat` JSON frames, the receive call does not time out, so the deadline is never enforced. The server replies to each heartbeat with a large `hb_ack`, but continues waiting for the final `answer` message.

## Exploit

1. Connect to `wss://choppediver.challs.umdctf.io:4443/`.
2. Receive `hello`.
3. Send `{"type":"ready"}`.
4. Continuously send `{"type":"heartbeat"}` in a second thread.
5. For each round:
   - receive the JSON round header;
   - receive the PNG;
   - classify the chart from the line slope:
     - upward trend -> `buy`;
     - downward trend -> `sell`;
     - near-flat trend -> `hold`;
   - send `{"type":"answer","value":"..."}`;
   - ignore `hb_ack` frames until receiving `correct`.
6. After 25 correct rounds, the server returns `{"type":"pass","flag":"..."}`.

## Why it works

The server accepts heartbeat messages inside `_play_round()` and does not check whether the deadline has already passed after processing them. The deadline is checked only in the `TimeoutError` branch. Therefore, as long as the inbound queue contains heartbeat messages, the timeout branch is avoided and the client gets enough time to solve the chart.

## Run

```bash
python3 -m pip install websockets pillow numpy
python3 solve_choppediver_short_optimized.py --url wss://choppediver.challs.umdctf.io:4443/ --insecure -v
```

If the service requires an Origin header:

```bash
python3 solve_choppediver_v2.py \
  --url wss://choppediver.challs.umdctf.io:4443/ \
  --origin https://choppediver.challs.umdctf.io \
  --insecure -v \
  --artifact-dir choppediver_artifacts_v2
```

The solver prints the flag to stdout.

---

## Extended analytical addendum

This addendum preserves the original Markdown content and extends it without deleting earlier sections. It avoids direct repetition by focusing on the reasoning dependencies, the sharper vulnerability taxonomy, the launch matrix, and the evidence chain that validates each established result.

### 1. Extended objectives and measurable success criteria

The solve path is easier to understand if it is written as a dependency chain rather than as a flat list of actions.

| Objective | Why it matters | Validation signal | Depends on |
|---|---|---|---|
| Identify the real service endpoint | A solver that stays on port 443 never reaches the game logic. | `hello` packet from `:4443` | None |
| Prove the timeout bug | Otherwise late rounds below 100 ms remain unexplained. | Late rounds succeed even though local classification takes longer than the nominal budget. | Correct endpoint |
| Stabilize BUY / SELL / HOLD | Once timing is bypassed, semantic accuracy becomes the only blocker. | No more `wrong: was hold` on flat charts. | Timeout bypass |
| Archive evidence | A final report should be reproducible, not just anecdotal. | `events.jsonl`, charts, summary, flag, zip | Working archival solver |
| Recover the flag | Final success condition. | `pass` packet and printed flag | All previous goals |

### 2. Reasoning dependency map

```text
A. Objective framing -> B. Endpoint proof -> C. Protocol exploit
                              |                    |
                              v                    v
                      D. Classification problem -> E. Pitfall resolution -> F. Final evidence
```

Interpretation:
- the endpoint proof is a prerequisite for every later statement;
- the protocol exploit is validated independently of the classifier because it changes `timeout` failures into semantic failures;
- the HOLD fix is a downstream correction once the timing issue has already been neutralized.

### 3. Dependency-aware proof chain

#### 3.1 Endpoint correction is a proven result

The first handshake failure with `HTTP 200` was not random. WSS without an explicit port targets `443`, which is the Assessment Portal front-end. The provided server code, however, binds the raw TLS WebSocket service to `4443`. This is why `wss://choppediver.challs.umdctf.io:4443/` is the only correct endpoint for the exploit path.

#### 3.2 The heartbeat bypass is proven by contradiction

The late rounds advertise budgets of `80 ms`, `40 ms`, `15 ms`, and `5 ms`, yet the successful logs show classification times far above those values. If the deadline were enforced strictly on every loop iteration, the solver would have failed with `timeout`. Because the application instead accepts the answers, the consistent explanation is that `heartbeat` traffic keeps `recv(timeout=0.001)` from timing out, so the deadline branch is never entered.

#### 3.3 The final classifier fix is evidence-driven

The remaining failures were not arbitrary; they clustered around flat charts with reject reasons of the form `wrong: was hold`. That pattern justified the move from a pure slope threshold to a morphology-aware rule based on `span_y` and `thickness_ratio`. The fix therefore follows directly from the observed failure mode.

### 4. Vulnerability and pitfall taxonomy

| Type | Item | Impact | Why it matters |
|---|---|---|---|
| Practical pitfall | Wrong endpoint on `443` | WebSocket handshake fails with `HTTP 200` | Must be corrected before any exploit reasoning is meaningful |
| Exploitable vulnerability | Deadline checked only after `recv()` timeout | Effective answer window can be extended | Core server-side weakness that unlocks the solve |
| Protocol amplifier | `heartbeat` allowed inside `_play_round()` | Keeps the receive loop busy and generates `hb_ack` traffic | Turns the timing flaw into a reliable exploit |
| Residual challenge difficulty | HOLD ambiguity on flat charts | Causes semantic mistakes rather than timeouts | Explains the last classifier tuning step |
| Operational difficulty | Ack queue management | Too many `hb_ack` frames can interfere with control flow | Justifies `--answer-quiet` in the v2 solver |

### 5. Encountered difficulties and how they were resolved

1. **Transport ambiguity.** The same hostname served both the friendly web portal and the raw WebSocket service. Source reading plus live handshake checks resolved the ambiguity.
2. **Mixed failure causes.** Early runs combined timeout risks and classifier mistakes. Reading the exact reject reason separated the two problems.
3. **Weak HOLD modeling.** Small slope values were not enough. Visual flatness plus thickness made the rule robust.
4. **Need for archival proof.** A concise solver alone is not ideal for a final write-up. The v2 solver adds durable evidence without changing the underlying exploit.

### 6. Launch modes, arguments, and shell wrappers

#### 6.1 Direct Python launches

```bash
# syntax check / pseudo-compile
python3 -m py_compile solve_choppediver_short_optimized.py solve_choppediver_v2.py solve_choppediver_all_in_one.py

# dependency installation
python3 -m pip install websockets pillow numpy

# short optimized solver
python3 solve_choppediver_short_optimized.py \\
  --url wss://choppediver.challs.umdctf.io:4443/ \\
  --insecure -v

# full archival solver
python3 solve_choppediver_v2.py \\
  --url wss://choppediver.challs.umdctf.io:4443/ \\
  --insecure -v \\
  --artifact-dir choppediver_artifacts_v2

# all-in-one archival solver
python3 solve_choppediver_all_in_one.py \\
  --url wss://choppediver.challs.umdctf.io:4443/ \\
  --insecure -v \\
  --artifact-dir choppediver_artifacts
```

#### 6.2 Available options by solver

- `solve_choppediver_short_optimized.py`
  - `--url`
  - `--origin`
  - `--insecure`
  - `-v`, `--verbose`

- `solve_choppediver_v2.py`
  - `--url`
  - `--insecure`
  - `--origin`
  - `--hb-interval`
  - `--answer-quiet`
  - `--threshold`
  - `--hold-span-px`
  - `--hold-thickness-ratio`
  - `--open-timeout`
  - `--artifact-dir`
  - `-v`, `--verbose`

- `solve_choppediver_all_in_one.py`
  - `--url`
  - `--insecure`
  - `--origin`
  - `--hb-interval`
  - `--answer-quiet`
  - `--threshold`
  - `--open-timeout`
  - `--artifact-dir`
  - `-v`, `--verbose`

#### 6.3 Shell wrappers

```bash
# v2 wrapper
./run_choppediver_v2.sh [artifact_dir]

# all-in-one archival wrapper
./run_choppediver_and_archive.sh [artifact_dir]

# optional endpoint override for the all-in-one wrapper
CHOPPEDIVER_URL=wss://choppediver.challs.umdctf.io:4443/ ./run_choppediver_and_archive.sh outdir
```

What they do:
- install dependencies automatically;
- launch the corresponding Python solver with safe defaults;
- for `run_choppediver_and_archive.sh`, print the resulting archive, log, summary, and flag locations.

### 7. Extended large synthesis

The challenge is best solved as a stacked reasoning exercise. The first layer is transport correctness: if the solver remains on port `443`, it never reaches the game. The second layer is protocol exploitation: the server itself creates a timing weakness by coupling deadline enforcement to `recv()` timeouts while also accepting heartbeats inside the round loop. The third layer is semantic accuracy: after the timing barrier is neutralized, flat HOLD charts become the remaining source of failure. The fourth layer is reproducibility: a complete write-up is stronger when the final run leaves behind charts, logs, summaries, and a zip archive.

That dependency chain also explains why the final narrative should not be flattened into a single sentence such as “spam heartbeat and classify the charts”. The endpoint proof, the contradiction-based timeout proof, the HOLD-specific classifier fix, and the archival evidence all depend on one another. Together they justify the recovered result: the service eventually returns `pass` and the final flag.

---

## Readable explanation addendum

This final addendum keeps the previous content intact and restates the most important ideas in a cleaner, more readable way.

The challenge can be understood as a chain of small discoveries.
Each step depends on the one before it.
That is why the write-up should not read like a random list of tricks.
It should read like a guided path from confusion to proof.

### A. Objectives, explained simply

At first, the challenge looks like a speed game.
A chart appears, and we are supposed to answer quickly whether it goes up, goes down, or stays flat.

But the real objective is broader than that.
We must understand how the service works, find the mistake in its timing logic, and then build a solver that uses that mistake to finish all rounds.

So the practical objective becomes:

- reach the real challenge service;
- understand how one round works;
- prove why the announced time limits are misleading;
- exploit the heartbeat weakness to gain enough effective time;
- classify BUY / SELL / HOLD correctly;
- survive all 25 rounds;
- recover the final flag and preserve evidence.

### B. Reasoning chain with dependencies

```text
[Find the right endpoint]
          |
          v
[Reach the real WebSocket service on :4443]
          |
          v
[Observe the protocol: hello -> ready -> round -> PNG -> answer]
          |
          v
[Read the server loop and notice the timeout weakness]
          |
          v
[Use heartbeat to keep recv() busy]
          |
          v
[Turn a "real-time" problem into a normal local analysis task]
          |
          v
[Improve HOLD detection for flat charts]
          |
          v
[Pass all rounds and recover the flag]
          |
          v
[Archive logs, charts, and the final proof]
```

This diagram matters because each result depends on the previous one.
If the endpoint is wrong, the exploit cannot even start.
If the timeout bug is not understood, the late rounds look impossible.
If HOLD is not modeled correctly, the exploit still fails even after the timing problem is neutralized.

### C. Logical state-transition boxes for the solving algorithm

```text
┌──────────────────────────────┐
│ 1. Connect to the service    │
│    wss://...:4443            │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 2. Receive hello             │
│    learn total round count   │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 3. Send ready                │
│    begin assessment          │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 4. Receive round JSON        │
│    read round number + timer │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 5. Receive PNG chart         │
│    one binary image          │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 6. Keep heartbeat alive      │
│    stop timeout enforcement  │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 7. Classify the chart        │
│    buy / sell / hold         │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 8. Send answer               │
│    JSON control message      │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 9. Ignore hb_ack frames      │
│    wait for useful result    │
└──────────────┬───────────────┘
               │
               v
      ┌──────────────────────┐
      │ correct / reject /   │
      │ pass ?               │
      └───────┬───────┬──────┘
              │       │
      correct │       │ reject
              │       │
              v       v
   [next round]   [inspect failure]
                      │
                      v
             [adjust endpoint, exploit,
              or classifier]

and if result == pass:

          [print flag]
               |
               v
         [write artifacts]
```

### D. Why the exploit works, in plain words

The server says that some rounds allow only a few milliseconds.
If that were enforced strictly, a normal chart classifier would lose.

However, the server has a weakness.
It only checks whether time is over when its receive function times out.
If we keep sending heartbeat messages, the receive function keeps getting work to do.
That means the code path that checks the time is postponed.

So the advertised timer is no longer the real limit.
We quietly create a larger effective answer window.

### E. Why classification still matters after the exploit

The heartbeat trick solves only the timing part.
It does not tell us whether a chart is BUY, SELL, or HOLD.

That is why some late runs still failed.
The logs showed errors like `wrong: was hold`.
This proved that the remaining weakness was not speed anymore, but label quality.

The fix was not a huge redesign.
It was a focused improvement for flat charts.
Instead of using only slope, the final classifier also uses the chart shape, especially `span_y` and `thickness_ratio`.
That makes flat HOLD charts easier to recognize.

### F. Launching modes, with breathing room

#### Short optimized solver

```bash
python3 -m pip install websockets pillow numpy

python3 solve_choppediver_short_optimized.py \
  --url wss://choppediver.challs.umdctf.io:4443/ \
  --insecure -v
```

Use this solver when you want a compact, direct exploit.
It is shorter and easier to audit quickly.

#### Full v2 archival solver

```bash
python3 -m pip install websockets pillow numpy

python3 solve_choppediver_v2.py \
  --url wss://choppediver.challs.umdctf.io:4443/ \
  --insecure -v \
  --artifact-dir choppediver_artifacts_v2
```

Use this solver when you want logs, saved charts, a summary file, the flag file, and a final archive.
This is the better option for a write-up or for preserving proof.

#### Wrapper scripts

```bash
./run_choppediver_v2.sh [artifact_dir]

./run_choppediver_and_archive.sh [artifact_dir]
```

The shell wrappers install dependencies and launch the matching Python solver with sensible defaults.
They are useful when you want a quick run without remembering every option manually.

### G. Final readable synthesis

In the end, this challenge is not really about being faster than a machine.
It is about understanding the service better than the challenge expected.

First, the solver had to reach the correct service on port `4443`.
Second, it had to exploit the timeout logic with heartbeat traffic.
Third, it had to fix the remaining classification ambiguity around HOLD.
Only after those three layers were solved in order did the flag become reachable.

That is the clean lesson of the challenge.
Do not only optimize the last visible step.
First understand the system, then break the weakest layer, and only then polish the remaining details.
