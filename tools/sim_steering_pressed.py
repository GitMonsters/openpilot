#!/usr/bin/env python3
"""
Replay raw EPS_Lenkmoment from a route through the steeringPressed debounce
with configurable threshold and min_count, and report flicker statistics.

Usage:
  uv run python tools/sim_steering_pressed.py aebd8f1d4ea16066/000000cc--ddab4e6155
  uv run python tools/sim_steering_pressed.py <route> --threshold 100 --count 5
  uv run python tools/sim_steering_pressed.py <route> --sweep
"""
import argparse
import numpy as np
from openpilot.tools.lib.logreader import LogReader
from opendbc.can.parser import CANParser
from opendbc.car import Bus
from opendbc.car.volkswagen.values import DBC, CAR


def simulate(torques, threshold, min_count):
  """Run update_steering_pressed semantics frame-by-frame."""
  cnt = 0
  out = []
  for t in torques:
    raw = abs(t) > threshold
    cnt += 1 if raw else -1
    cnt = int(np.clip(cnt, 0, min_count * 2 + 1))
    out.append(cnt > min_count)
  return out


def intervals(pressed):
  """Return list of (start_idx, end_idx, duration_frames) for each True run."""
  out, start = [], None
  for i, p in enumerate(pressed):
    if p and start is None:
      start = i
    elif not p and start is not None:
      out.append((start, i, i - start))
      start = None
  if start is not None:
    out.append((start, len(pressed), len(pressed) - start))
  return out


def summarize(label, torques, threshold, min_count, frame_ms):
  pressed = simulate(torques, threshold, min_count)
  ivs = intervals(pressed)
  durs_ms = [d * frame_ms for _, _, d in ivs]
  flicker = sum(1 for d in durs_ms if d < 100)
  short = sum(1 for d in durs_ms if 100 <= d < 500)
  real = sum(1 for d in durs_ms if d >= 500)
  total_true_frames = sum(pressed)
  print(f"  {label:30s} intervals={len(ivs):3d}  flicker(<100ms)={flicker:3d}  short(100-500ms)={short:3d}  real(>=500ms)={real:3d}  total_True_frames={total_true_frames}")


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--threshold", type=int, default=100, help="STEER_DRIVER_ALLOWANCE")
  ap.add_argument("--count", type=int, default=5, help="min_count for debounce")
  ap.add_argument("--sweep", action="store_true", help="sweep thresholds and min_counts")
  args = ap.parse_args()

  parser = CANParser(DBC[CAR.VOLKSWAGEN_ID4_MK1][Bus.pt], [("LH_EPS_03", float('nan'))], 0)
  lr = LogReader(args.route)

  ts, torques = [], []
  for msg in lr:
    if msg.which() != "can":
      continue
    parser.update([msg.logMonoTime, [(c.address, c.dat, c.src) for c in msg.can if c.src == 0]])
    vl = parser.vl["LH_EPS_03"]
    signed = vl["EPS_Lenkmoment"] * (1 if vl["EPS_VZ_Lenkmoment"] == 0 else -1)
    ts.append(msg.logMonoTime)
    torques.append(signed)

  if not torques:
    print("no LH_EPS_03 samples in route")
    return
  # estimate frame rate from CAN timestamps
  dts = np.diff(ts)
  frame_ms = float(np.median(dts)) / 1e6 if len(dts) else 10.0
  print(f"route: {args.route}")
  print(f"LH_EPS_03 samples: {len(torques)}   median frame interval: {frame_ms:.1f} ms")
  abs_t = np.abs(torques)
  print(f"abs(torque) p50={np.percentile(abs_t, 50):.0f}  p90={np.percentile(abs_t, 90):.0f}  p99={np.percentile(abs_t, 99):.0f}  max={int(abs_t.max())}")
  print()

  if args.sweep:
    print("Sweep:")
    for thr in (60, 80, 100, 120):
      for cnt in (5, 10, 15, 20):
        summarize(f"thr={thr} cnt={cnt}", torques, thr, cnt, frame_ms)
      print()
  else:
    summarize(f"thr={args.threshold} cnt={args.count} (current)", torques, args.threshold, args.count, frame_ms)


if __name__ == "__main__":
  main()
