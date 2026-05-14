#!/usr/bin/env python3
"""
Inspect take-over / steerSaturated alert quality from a route's qlog.

Pulls:
  - carControl.actuators.curvature        (what selfdrive asked for)
  - carOutput.actuatorsOutput.curvature   (what carcontroller actually sent)
  - controlsState.lateralControlState.*.saturated
  - onroadEvents.steerSaturated           (the upstream event)
  - selfdriveState.alertType/Text         (the actual user-visible alert)
  - carState.vEgo / steeringPressed / standstill

Reports:
  1. Distinct steerSaturated/warning firings (user-visible alerts).
  2. Carcontroller-side curvature clipping (carControl vs carOutput) — where the
     carcontroller silently chopped commanded curvature.
  3. Overlap: how many clips preceded an alert, how many alerts had no clip,
     how many clips had no alert (= "should-have-warned").

Usage:
  uv run python tools/sim_saturation.py <route>
  uv run python tools/sim_saturation.py <route> --label "VW_MEB"
"""
import argparse
import numpy as np
from openpilot.tools.lib.logreader import LogReader


def collect(route):
  lr = LogReader(route + "/q")
  cc, co, cs, lac, fire = [], [], [], [], []
  for msg in lr:
    w = msg.which()
    t = msg.logMonoTime
    if w == "carControl":
      cc.append((t, msg.carControl.actuators.curvature, msg.carControl.latActive))
    elif w == "carOutput":
      co.append((t, msg.carOutput.actuatorsOutput.curvature))
    elif w == "carState":
      s = msg.carState
      cs.append((t, s.vEgo, s.steeringPressed))
    elif w == "controlsState":
      st = msg.controlsState.lateralControlState
      which = st.which()
      sat = False
      if which == "angleState":
        sat = st.angleState.saturated
      elif which == "torqueState":
        sat = st.torqueState.saturated
      elif which == "pidState":
        sat = st.pidState.saturated
      lac.append((t, which, sat))
    elif w == "selfdriveState":
      at = str(msg.selfdriveState.alertType)
      fire.append((t, at))
  return cc, co, cs, lac, fire


def alert_firings(fire, alert_type):
  prev = ""
  out = []
  for t, at in fire:
    if at == alert_type and prev != alert_type:
      out.append(t)
    prev = at
  return out


def detect_clip_intervals(cc, co, abs_eps=0.005, rel_eps=0.05, min_frames=3):
  """Return intervals where |cmd - out| > max(abs_eps, rel_eps*|cmd|) sustained."""
  cc_t = np.array([t for t, _, _ in cc])
  cc_c = np.array([c for _, c, _ in cc])
  co_t = np.array([t for t, _ in co])
  co_c = np.array([c for _, c in co])
  # Align: for each cc sample, find nearest co
  intervals = []
  start = None
  run = 0
  for i, t in enumerate(cc_t):
    j = min(np.searchsorted(co_t, t), len(co_c) - 1)
    diff = abs(cc_c[i] - co_c[j])
    threshold = max(abs_eps, rel_eps * abs(cc_c[i]))
    if diff > threshold:
      if start is None:
        start = i
      run += 1
    else:
      if start is not None and run >= min_frames:
        intervals.append((cc_t[start], cc_t[i - 1], cc_c[start:i].max() - cc_c[start:i].min(), run))
      start = None
      run = 0
  if start is not None and run >= min_frames:
    intervals.append((cc_t[start], cc_t[-1], 0, run))
  return intervals


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--label", default="")
  args = ap.parse_args()

  cc, co, cs, lac, fire = collect(args.route)
  label = args.label or args.route

  # 1. Firings of steerSaturated warning
  fires = alert_firings(fire, "steerSaturated/warning")
  # 2. Carcontroller clip intervals (cmd vs out)
  clips = detect_clip_intervals(cc, co) if cc and co else []
  # 3. controlsState lateral saturation rising edges
  lac_sat_edges = []
  last = False
  ctrl_type = lac[0][1] if lac else "?"
  for t, w, s in lac:
    if s and not last:
      lac_sat_edges.append(t)
    last = s

  # Cross-correlate: for each clip, was there an alert within 0..3000ms after?
  WINDOW = 3_000_000_000  # 3s
  clips_with_alert = 0
  for cs_start, cs_end, _, _ in clips:
    if any(cs_start <= f <= cs_end + WINDOW for f in fires):
      clips_with_alert += 1

  alerts_with_clip = 0
  for f in fires:
    if any(cs_start - WINDOW <= f <= cs_end + WINDOW for cs_start, cs_end, _, _ in clips):
      alerts_with_clip += 1

  print(f"\n=== {label} ===")
  print(f"  route: {args.route}")
  print(f"  carControl samples: {len(cc)}   carOutput samples: {len(co)}")
  print(f"  lateralControlState type: {ctrl_type}")
  print(f"  controlsState saturated rising edges: {len(lac_sat_edges)}")
  print(f"  steerSaturated/warning user-visible firings: {len(fires)}")
  print(f"  carcontroller clip intervals (cmd vs out diverge >=5mr/m for >=30ms): {len(clips)}")
  if clips:
    longest = max((cs_end - cs_start for cs_start, cs_end, _, _ in clips)) / 1e6
    print(f"  longest clip interval: {longest:.0f}ms")
  print(f"  clips that produced an alert within 3s: {clips_with_alert}/{len(clips)}")
  print(f"  alerts preceded by a clip within 3s: {alerts_with_clip}/{len(fires)}  (orphan alerts: {len(fires) - alerts_with_clip})")


if __name__ == "__main__":
  main()
