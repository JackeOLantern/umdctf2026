#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import ssl
import sys
import threading
import time
import traceback
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from websockets.sync.client import connect

HEARTBEAT = json.dumps({"type": "heartbeat"}, separators=(",", ":"))


@dataclass
class Prediction:
    value: str
    score: float
    dy_pixels: float
    span_x: int
    span_y: int
    mask_pixels: int
    thickness_ratio: float
    slope_lr: float


class EventLogger:
    def __init__(self, artifact_dir: Path, verbose: bool = False):
        self.artifact_dir = artifact_dir
        self.verbose = verbose
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.artifact_dir / "events.jsonl"
        self.summary_path = self.artifact_dir / "summary.txt"
        self.flag_path = self.artifact_dir / "flag.txt"
        self.console_path = self.artifact_dir / "console.log"
        self.charts_dir = self.artifact_dir / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._t0 = time.time()
        self._fh = self.log_path.open("a", encoding="utf-8")
        self._console = self.console_path.open("a", encoding="utf-8")
        self.flag: str | None = None
        self.status = "started"
        self.rounds_seen = 0
        self.rounds_correct = 0
        self.heartbeats_sent = 0
        self.hb_acks_skipped = 0
        self.last_error: str | None = None
        self.log("session_start", argv=sys.argv, cwd=os.getcwd())

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            finally:
                self._console.flush()
                self._console.close()

    def console(self, msg: str) -> None:
        with self._lock:
            print(msg, file=sys.stderr, flush=True)
            self._console.write(msg + "\n")
            self._console.flush()

    def log(self, event: str, **data: Any) -> None:
        rec = {
            "ts": time.time(),
            "elapsed_s": round(time.time() - self._t0, 6),
            "event": event,
            **data,
        }
        with self._lock:
            self._fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
            self._fh.flush()

    def save_chart(self, round_no: int, png: bytes) -> Path:
        path = self.charts_dir / f"round_{round_no:02d}.png"
        path.write_bytes(png)
        self.log("chart_saved", round=round_no, path=str(path), size=len(png))
        return path

    def write_summary(self) -> None:
        lines = [
            "Choppediver OA run summary",
            f"status: {self.status}",
            f"rounds_seen: {self.rounds_seen}",
            f"rounds_correct: {self.rounds_correct}",
            f"heartbeats_sent: {self.heartbeats_sent}",
            f"hb_acks_skipped: {self.hb_acks_skipped}",
        ]
        if self.flag:
            lines.append(f"flag: {self.flag}")
            self.flag_path.write_text(self.flag + "\n", encoding="utf-8")
        if self.last_error:
            lines.append(f"error: {self.last_error}")
        self.summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def make_archive(self) -> Path:
        archive_path = self.artifact_dir / "choppediver_artifacts.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.artifact_dir.rglob("*")):
                if path == archive_path or path.is_dir():
                    continue
                zf.write(path, path.relative_to(self.artifact_dir))
        self.log("archive_written", path=str(archive_path))
        return archive_path


# -------- chart extraction / classification --------

def longest_nearly_contiguous_run(xs: np.ndarray, max_gap: int = 3) -> np.ndarray:
    if xs.size == 0:
        return xs
    best_start = start = 0
    best_len = 1
    for i in range(1, xs.size):
        if xs[i] - xs[i - 1] > max_gap:
            cur_len = i - start
            if cur_len > best_len:
                best_start, best_len = start, cur_len
            start = i
    cur_len = xs.size - start
    if cur_len > best_len:
        best_start, best_len = start, cur_len
    return xs[best_start : best_start + best_len]


def series_from_mask(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = mask.shape
    col_counts = mask.sum(axis=0)
    xs = np.flatnonzero(col_counts > 0)
    if xs.size == 0:
        return np.array([], dtype=int), np.array([], dtype=float)
    run = longest_nearly_contiguous_run(xs)
    if run.size < max(20, int(0.15 * w)):
        run = xs
    ys: list[float] = []
    keep_xs: list[int] = []
    for x in run:
        yv = np.flatnonzero(mask[:, x])
        if yv.size:
            keep_xs.append(int(x))
            ys.append(float(np.median(yv)))
    return np.asarray(keep_xs, dtype=int), np.asarray(ys, dtype=float)


def smooth1d(arr: np.ndarray, win: int) -> np.ndarray:
    if arr.size == 0 or win <= 1:
        return arr.copy()
    if win % 2 == 0:
        win += 1
    if arr.size < win:
        return arr.copy()
    pad = win // 2
    ext = np.pad(arr, (pad, pad), mode="edge")
    ker = np.ones(win, dtype=float) / win
    return np.convolve(ext, ker, mode="valid")


def classify_chart_png(
    png: bytes,
    base_threshold: float = 0.06,
    hold_span_px: int = 230,
    hold_thickness_ratio: float = 80.0,
) -> Prediction:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(im).astype(np.int16)
    h, w, _ = arr.shape

    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    sat = maxc - minc
    mean = arr.mean(axis=2)

    # Prefer colored plot traces while suppressing bright background / axes.
    mask = (sat > 35) & (maxc < 250) & (minc < 245)

    y0, y1 = int(0.04 * h), int(0.96 * h)
    x0, x1 = int(0.03 * w), int(0.97 * w)
    mask[:y0, :] = False
    mask[y1:, :] = False
    mask[:, :x0] = False
    mask[:, x1:] = False

    # Fallback when the trace is darker than it is saturated.
    if int(mask.sum()) < max(80, w // 3):
        mask = (mean < 210) & (maxc < 245)
        mask[:y0, :] = False
        mask[y1:, :] = False
        mask[:, :x0] = False
        mask[:, x1:] = False
        row_counts = mask.sum(axis=1)
        col_counts = mask.sum(axis=0)
        mask[row_counts > int(0.35 * w), :] = False
        mask[:, col_counts > int(0.35 * h)] = False

    xs, ys = series_from_mask(mask)
    if xs.size < 10:
        ys_all, xs_all = np.nonzero(mask)
        if xs_all.size < 10:
            return Prediction("hold", 0.0, 0.0, 0, 0, int(mask.sum()), 0.0, 0.0)
        order = np.argsort(xs_all)
        xs = xs_all[order].astype(int)
        ys = ys_all[order].astype(float)

    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    ys_sm = smooth1d(ys, max(5, len(ys) // 40))

    n = xs.size
    k = max(8, n // 6)
    left_y = float(np.median(ys_sm[:k]))
    right_y = float(np.median(ys_sm[-k:]))
    dy = left_y - right_y
    score = dy / max(1, h)

    span_y = max(1, int(np.percentile(ys_sm, 95) - np.percentile(ys_sm, 5)))
    span_x = max(1, int(xs[-1] - xs[0]))
    mask_pixels = int(mask.sum())
    thickness_ratio = mask_pixels / max(1.0, span_y)

    # Robust global trend via linear regression on the extracted median trace.
    x_norm = (xs - xs[0]) / max(1.0, xs[-1] - xs[0])
    y_norm = ys_sm / max(1.0, h)
    if len(xs) >= 2:
        slope_lr = float(np.polyfit(x_norm, y_norm, 1)[0])
    else:
        slope_lr = 0.0

    # Heuristic from observed challenge runs:
    # true HOLD charts are much flatter vertically and much thicker as masks.
    near_flat_band = span_y <= hold_span_px and thickness_ratio >= hold_thickness_ratio
    near_zero_trend = abs(score) <= base_threshold and span_y <= hold_span_px + 20
    reg_flat = abs(slope_lr) <= max(base_threshold * 0.9, 0.05) and span_y <= hold_span_px

    if near_flat_band or (near_zero_trend and reg_flat):
        value = "hold"
    else:
        # combine left-right delta and regression sign for stability
        combo = 0.65 * score - 0.35 * slope_lr
        if abs(combo) <= max(base_threshold * 0.8, 0.045) and span_y <= hold_span_px:
            value = "hold"
        elif combo > 0:
            value = "buy"
        else:
            value = "sell"

    return Prediction(value, float(score), float(dy), int(span_x), int(span_y), mask_pixels, float(thickness_ratio), slope_lr)


class Heartbeater:
    def __init__(self, ws: Any, send_lock: threading.Lock, interval: float, logger: EventLogger):
        self.ws = ws
        self.send_lock = send_lock
        self.interval = interval
        self.logger = logger
        self.stop_event = threading.Event()
        self.enabled = threading.Event()
        self.thread = threading.Thread(target=self._run, name="heartbeat-spammer", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.logger.log("heartbeat_thread_started", interval=self.interval)

    def stop(self) -> None:
        self.stop_event.set()
        self.enabled.set()
        self.thread.join(timeout=1.0)
        self.logger.log("heartbeat_thread_stopped", sent=self.logger.heartbeats_sent)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            if not self.enabled.wait(timeout=0.05):
                continue
            try:
                with self.send_lock:
                    self.ws.send(HEARTBEAT)
                self.logger.heartbeats_sent += 1
            except Exception as exc:
                self.logger.log("heartbeat_send_error", error=repr(exc))
                return
            if self.interval > 0:
                time.sleep(self.interval)


def recv_json_skip_hb(ws: Any, logger: EventLogger, verbose: bool = False) -> dict[str, Any]:
    skipped = 0
    while True:
        msg = ws.recv()
        if isinstance(msg, bytes):
            raise RuntimeError(f"expected JSON frame, got {len(msg)} binary bytes")
        obj = json.loads(msg)
        if obj.get("type") == "hb_ack":
            skipped += 1
            logger.hb_acks_skipped += 1
            continue
        if skipped:
            logger.log("hb_ack_skipped_batch", count=skipped)
        logger.log("json_received", payload=obj)
        if verbose:
            logger.console(f"<= {obj}")
        return obj


def recv_round_png(ws: Any, logger: EventLogger, verbose: bool = False) -> tuple[dict[str, Any], bytes]:
    while True:
        obj = recv_json_skip_hb(ws, logger=logger, verbose=verbose)
        typ = obj.get("type")
        if typ == "round":
            break
        if typ == "pass":
            return obj, b""
        if typ == "reject":
            raise RuntimeError(f"server rejected: {obj}")
    while True:
        msg = ws.recv()
        if isinstance(msg, bytes):
            logger.log("png_received", size=len(msg), round=obj.get("round"))
            return obj, msg
        obj2 = json.loads(msg)
        if obj2.get("type") == "hb_ack":
            logger.hb_acks_skipped += 1
            continue
        raise RuntimeError(f"expected PNG after round header, got {obj2}")


def normalize_url(target: str) -> str:
    if target.startswith("ws://") or target.startswith("wss://"):
        return target
    return "wss://" + target.rstrip("/") + "/"


def solve(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir)
    logger = EventLogger(artifact_dir=artifact_dir, verbose=args.verbose)
    url = normalize_url(args.url)
    ctx = ssl._create_unverified_context() if args.insecure else ssl.create_default_context()
    send_lock = threading.Lock()
    headers = [("Origin", args.origin)] if args.origin else None

    try:
        logger.log(
            "connect_start",
            url=url,
            insecure=args.insecure,
            hb_interval=args.hb_interval,
            answer_quiet=args.answer_quiet,
            threshold=args.threshold,
            hold_span_px=args.hold_span_px,
            hold_thickness_ratio=args.hold_thickness_ratio,
        )
        with connect(
            url,
            ssl=ctx,
            max_size=None,
            max_queue=None,
            open_timeout=args.open_timeout,
            ping_interval=None,
            proxy=None,
            additional_headers=headers,
        ) as ws:
            hello = recv_json_skip_hb(ws, logger=logger, verbose=args.verbose)
            if hello.get("type") != "hello":
                raise RuntimeError(f"expected hello, got {hello}")
            total = int(hello.get("total", 25))
            logger.log("connected", total=total)
            logger.console(f"[+] connected: total={total}")

            hb = Heartbeater(ws, send_lock, args.hb_interval, logger)
            hb.start()
            try:
                with send_lock:
                    ready_payload = {"type": "ready"}
                    ws.send(json.dumps(ready_payload, separators=(",", ":")))
                logger.log("ready_sent", payload=ready_payload)
                hb.enabled.set()
                logger.log("heartbeat_enabled")

                for i in range(1, total + 1):
                    round_info, png = recv_round_png(ws, logger=logger, verbose=args.verbose)
                    if round_info.get("type") == "pass":
                        logger.flag = str(round_info.get("flag", ""))
                        logger.status = "success"
                        logger.log("pass_received", flag=logger.flag)
                        print(logger.flag)
                        return 0

                    logger.rounds_seen = max(logger.rounds_seen, i)
                    round_no = int(round_info.get("round", i))
                    logger.log("round_header", round=round_no, payload=round_info)
                    logger.save_chart(round_no, png)

                    t0 = time.perf_counter()
                    pred = classify_chart_png(
                        png,
                        base_threshold=args.threshold,
                        hold_span_px=args.hold_span_px,
                        hold_thickness_ratio=args.hold_thickness_ratio,
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    logger.log("prediction", round=round_no, classify_ms=elapsed_ms, **asdict(pred))
                    logger.console(
                        f"[r{round_no:02d}] time={round_info.get('time_ms')}ms "
                        f"answer={pred.value} score={pred.score:+.4f} slope={pred.slope_lr:+.4f} "
                        f"dy={pred.dy_pixels:+.1f}px span=({pred.span_x},{pred.span_y}) "
                        f"mask={pred.mask_pixels} thick={pred.thickness_ratio:.1f} "
                        f"classify={elapsed_ms:.2f}ms"
                    )

                    hb.enabled.clear()
                    logger.log("heartbeat_disabled_for_answer", round=round_no)
                    if args.answer_quiet > 0:
                        time.sleep(args.answer_quiet)

                    answer_payload = {"type": "answer", "value": pred.value}
                    with send_lock:
                        ws.send(json.dumps(answer_payload, separators=(",", ":")))
                    logger.log("answer_sent", round=round_no, payload=answer_payload)

                    result = recv_json_skip_hb(ws, logger=logger, verbose=args.verbose)
                    logger.log("round_result", round=round_no, payload=result)
                    typ = result.get("type")
                    if typ == "correct":
                        logger.rounds_correct += 1
                        hb.enabled.set()
                        logger.log("heartbeat_reenabled", round=round_no)
                        continue
                    if typ == "pass":
                        logger.rounds_correct += 1
                        logger.flag = str(result.get("flag", ""))
                        logger.status = "success"
                        logger.log("pass_received", round=round_no, flag=logger.flag)
                        print(logger.flag)
                        return 0
                    logger.status = "failed"
                    logger.last_error = f"round {round_no}: {result}"
                    logger.console(f"[-] failed on round {round_no}: {result}")
                    return 2

                final = recv_json_skip_hb(ws, logger=logger, verbose=args.verbose)
                logger.log("final_message", payload=final)
                if final.get("type") == "pass":
                    logger.flag = str(final.get("flag", ""))
                    logger.status = "success"
                    logger.log("pass_received", flag=logger.flag)
                    print(logger.flag)
                    return 0
                logger.status = "failed"
                logger.last_error = f"no pass: {final}"
                logger.console(f"[-] no pass: {final}")
                return 3
            finally:
                hb.stop()
    except KeyboardInterrupt:
        logger.status = "interrupted"
        logger.last_error = "keyboard interrupt"
        logger.log("interrupted")
        raise
    except Exception as exc:
        logger.status = "error"
        logger.last_error = f"{exc.__class__.__name__}: {exc}"
        logger.log("exception", error=logger.last_error, traceback=traceback.format_exc())
        logger.console(f"[!] {logger.last_error}")
        return 1
    finally:
        logger.write_summary()
        archive_path = logger.make_archive()
        logger.console(f"[+] artifacts: {archive_path}")
        logger.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Solve UMDCTF Choppediver OA and archive the run")
    ap.add_argument("--url", default="wss://choppediver.challs.umdctf.io:4443/", help="WSS URL or host")
    ap.add_argument("--insecure", action="store_true", help="disable TLS certificate verification")
    ap.add_argument("--origin", default=None, help="optional Origin header")
    ap.add_argument("--hb-interval", type=float, default=0.0015, help="seconds between heartbeat frames")
    ap.add_argument("--answer-quiet", type=float, default=0.003, help="seconds to pause heartbeats before sending answer")
    ap.add_argument("--threshold", type=float, default=0.06, help="base normalized slope threshold for HOLD")
    ap.add_argument("--hold-span-px", type=int, default=230, help="max vertical span for thick HOLD bands")
    ap.add_argument("--hold-thickness-ratio", type=float, default=80.0, help="mask_pixels/span_y threshold used to force HOLD")
    ap.add_argument("--open-timeout", type=float, default=10.0, help="WebSocket connect timeout")
    ap.add_argument("--artifact-dir", default="choppediver_artifacts_v2", help="directory for logs, PNGs, summary, archive")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    try:
        raise SystemExit(solve(args))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
