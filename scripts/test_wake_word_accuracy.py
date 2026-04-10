#!/usr/bin/env python3
"""
Test openWakeWord wake model scores (matches hub: 16 kHz mono int16, ONNX inference).

Default model: app/backend/models/wake/pixel.onnx

From repo root:
  python scripts/test_wake_word_accuracy.py --wav recordings/pixel_take1.wav
  python scripts/test_wake_word_accuracy.py --positives wavs/wake --negatives wavs/other --threshold 0.55
  python scripts/test_wake_word_accuracy.py --mic --seconds 5

Optional live mic: pip install sounddevice
"""
from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path

import numpy as np

BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "app", "backend"))
DEFAULT_MODEL = Path(BACKEND) / "models" / "wake" / "pixel.onnx"
SAMPLE_RATE = 16000


def _backend_on_path() -> None:
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)


def load_wav_int16_mono_16k(path: Path) -> np.ndarray:
    """Load WAV; convert to mono int16 at 16 kHz (linear resample if rate differs)."""
    with wave.open(str(path), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if sw != 2:
        raise ValueError(f"{path}: need 16-bit PCM (sampwidth={sw}), convert with ffmpeg/sox")

    x = np.frombuffer(raw, dtype="<i2").copy()
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1).astype(np.int16)

    if rate == SAMPLE_RATE:
        return x

    # Linear resample to 16 kHz (good enough for wake-word evaluation)
    dur = len(x) / float(rate)
    new_n = max(1, int(round(dur * SAMPLE_RATE)))
    old_idx = np.arange(len(x), dtype=np.float64)
    new_idx = np.linspace(0.0, len(x) - 1.0, new_n)
    y = np.interp(new_idx, old_idx, x.astype(np.float64))
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


def build_model(model_path: Path):
    from openwakeword.model import Model as OwwModel

    if not model_path.is_file():
        raise FileNotFoundError(f"Model not found: {model_path}")
    return OwwModel(wakeword_models=[str(model_path)], inference_framework="onnx")


def max_score_dict(scores: dict) -> float:
    if not scores:
        return 0.0
    best = 0.0
    for v in scores.values():
        try:
            best = max(best, float(v))
        except (TypeError, ValueError):
            continue
    return best


def run_file(
    model,
    pcm: np.ndarray,
    chunk_samples: int,
    threshold: float,
) -> tuple[float, float, int, list[tuple[int, float]]]:
    """
    Returns (max_score, mean_chunk_max, chunks_ge_threshold, peaks as (offset_samples, score)).
    """
    model.reset()
    if pcm.size == 0:
        return 0.0, 0.0, 0, []

    peaks: list[tuple[int, float]] = []
    chunk_maxes: list[float] = []
    overall = 0.0
    offset = 0

    for start in range(0, len(pcm), chunk_samples):
        chunk = pcm[start : start + chunk_samples]
        if chunk.size == 0:
            continue
        sc = model.predict(chunk)
        mx = max_score_dict(sc)
        chunk_maxes.append(mx)
        overall = max(overall, mx)
        peaks.append((offset, mx))
        offset += int(chunk.size)

    mean_cm = float(np.mean(chunk_maxes)) if chunk_maxes else 0.0
    above = sum(1 for m in chunk_maxes if m >= threshold)
    return overall, mean_cm, above, peaks


def collect_wavs(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".wav", ".wave"):
            out.append(p)
    return out


def main() -> int:
    _backend_on_path()

    p = argparse.ArgumentParser(description="Test wake word ONNX accuracy / scores")
    p.add_argument(
        "--model",
        type=Path,
        default=Path(os.getenv("OMNIBOT_WAKE_WORD_MODEL", "") or DEFAULT_MODEL),
        help="Path to .onnx wake model (default: pixel.onnx or OMNIBOT_WAKE_WORD_MODEL)",
    )
    p.add_argument("--wav", type=Path, help="Single WAV to score")
    p.add_argument("--positives", type=Path, help="Directory of WAVs that should trigger (wake)")
    p.add_argument("--negatives", type=Path, help="Directory of WAVs that should not trigger")
    p.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("OMNIBOT_WAKE_THRESHOLD", "0.55")),
        help="Wake trigger threshold (same semantics as hub OMNIBOT_WAKE_THRESHOLD)",
    )
    p.add_argument(
        "--chunk-samples",
        type=int,
        default=1024,
        help="PCM samples per predict() call (hub Pixel stream uses 2048 bytes = 1024 samples)",
    )
    p.add_argument("--peaks", action="store_true", help="Print per-chunk peak scores for --wav")
    p.add_argument("--mic", action="store_true", help="Record from default mic (needs sounddevice)")
    p.add_argument("--seconds", type=float, default=5.0, help="Mic capture length (with --mic)")
    args = p.parse_args()

    try:
        model = build_model(args.model.resolve())
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        return 1

    label = args.model.stem
    print(f"Model: {args.model.resolve()}  (label={label})  threshold={args.threshold}")

    if args.mic:
        try:
            import sounddevice as sd
        except ImportError:
            print("Install sounddevice for --mic:  pip install sounddevice", file=sys.stderr)
            return 1
        n = int(max(0.5, args.seconds) * SAMPLE_RATE)
        print(f"Recording {args.seconds:.1f}s from default input @ {SAMPLE_RATE} Hz mono...")
        pcm = sd.rec(n, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        pcm = np.squeeze(pcm)
        mx, mean_cm, above, peaks = run_file(model, pcm, args.chunk_samples, args.threshold)
        print(f"max_score={mx:.4f}  mean_chunk_max={mean_cm:.4f}  chunks_ge_threshold={above}")
        print(f"triggered={mx >= args.threshold}")
        if args.peaks:
            for off, s in peaks:
                t = off / SAMPLE_RATE
                print(f"  t={t:6.3f}s  max={s:.4f}")
        return 0

    if args.wav:
        path = args.wav.resolve()
        pcm = load_wav_int16_mono_16k(path)
        mx, mean_cm, above, peaks = run_file(model, pcm, args.chunk_samples, args.threshold)
        dur = len(pcm) / SAMPLE_RATE
        print(f"{path.name}  duration={dur:.2f}s")
        print(f"max_score={mx:.4f}  mean_chunk_max={mean_cm:.4f}  chunks_ge_threshold={above}")
        print(f"triggered={mx >= args.threshold}")
        if args.peaks:
            for off, s in peaks:
                t = off / SAMPLE_RATE
                print(f"  t={t:6.3f}s  max={s:.4f}")
        return 0

    if args.positives or args.negatives:
        pos_dir = args.positives.resolve() if args.positives else None
        neg_dir = args.negatives.resolve() if args.negatives else None
        pos_files = collect_wavs(pos_dir) if pos_dir and pos_dir.is_dir() else []
        neg_files = collect_wavs(neg_dir) if neg_dir and neg_dir.is_dir() else []

        if pos_dir and not pos_dir.is_dir():
            print(f"Not a directory: {pos_dir}", file=sys.stderr)
            return 1
        if neg_dir and not neg_dir.is_dir():
            print(f"Not a directory: {neg_dir}", file=sys.stderr)
            return 1
        if not pos_files and not neg_files:
            print("No WAV files found under --positives / --negatives.", file=sys.stderr)
            return 1

        tp = fp = fn = tn = 0
        failures: list[str] = []

        for f in pos_files:
            try:
                pcm = load_wav_int16_mono_16k(f)
                mx, _, _, _ = run_file(model, pcm, args.chunk_samples, args.threshold)
            except Exception as e:
                failures.append(f"{f}: {e}")
                continue
            ok = mx >= args.threshold
            if ok:
                tp += 1
            else:
                fn += 1
                failures.append(f"{f}: max_score={mx:.4f} (expected >= {args.threshold})")

        for f in neg_files:
            try:
                pcm = load_wav_int16_mono_16k(f)
                mx, _, _, _ = run_file(model, pcm, args.chunk_samples, args.threshold)
            except Exception as e:
                failures.append(f"{f}: {e}")
                continue
            ok = mx < args.threshold
            if ok:
                tn += 1
            else:
                fp += 1
                failures.append(f"{f}: max_score={mx:.4f} (expected < {args.threshold})")

        n_pos, n_neg = tp + fn, fp + tn
        print(f"positives: {n_pos} files  |  negatives: {n_neg} files  |  threshold={args.threshold}")
        print(f"TP={tp}  FN={fn}  FP={fp}  TN={tn}")
        if n_pos:
            print(f"True positive rate (recall): {tp / n_pos:.2%}")
        if n_neg:
            print(f"True negative rate (specificity): {tn / n_neg:.2%}")
        if n_pos + n_neg:
            acc = (tp + tn) / (n_pos + n_neg)
            print(f"Accuracy: {acc:.2%}")
        if failures:
            print("\nMismatches / errors:")
            for line in failures:
                print(f"  {line}")
        return 0

    p.print_help()
    print("\nProvide --wav, --mic, or both --positives and/or --negatives.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
