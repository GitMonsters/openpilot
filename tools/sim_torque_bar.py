#!/usr/bin/env python3
"""
Replay a route to evaluate the MEB torque bar visualisation and the steerSaturated /
take-over warning, given the current carcontroller power logic.

Reproduces:
  - steering_power frame-by-frame (the value behind carOutput.actuatorsOutput.torque)
  - the "bar full" intervals (power == STEERING_POWER_MAX)
  - the "bar empty" intervals (power near STEERING_POWER_MIN)
  - commanded curvature near the safety limit (CURVATURE_MAX from values.py)
  - onroadEvents: steerSaturated and any visualAlert == steerRequired/ldw

Usage:
  uv run python tools/sim_torque_bar.py <route>
  uv run python tools/sim_torque_bar.py <route> --threshold 100
"""
import argparse
import numpy as np
from collections import defaultdict
from openpilot.tools.lib.logreader import LogReader
from opendbc.can.parser import CANParser
from opendbc.car import Bus
from opendbc.car.volkswagen.values import DBC, CAR

# MEB defaults — keep in sync with values.py
STEER_DRIVER_MAX = 300
STEERING_POWER_MAX = 50
STEERING_POWER_MIN = 4
STEERING_POWER_STEP = 2
CURVATURE_MAX = 0.195  # CCP.CURVATURE_LIMITS.CURVATURE_MAX
SAT_FRAC = 0.97  # treat |curv| > frac*CURVATURE_MAX as "near safety limit"


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--threshold", type=int, default=100, help="STEER_DRIVER_ALLOWANCE")
  args = ap.parse_args()

  # 1. Raw EPS torque from CAN (per frame, signed)
  parser = CANParser(DBC[CAR.VOLKSWAGEN_ID4_MK1][Bus.pt], [("LH_EPS_03", float('nan'))], 0)
  can_t, eps_torque = [], []
  for msg in LogReader(args.route):
    if msg.which() != "can":
      continue
    parser.update([msg.logMonoTime, [(c.address, c.dat, c.src) for c in msg.can if c.src == 0]])
    vl = parser.vl["LH_EPS_03"]
    signed = vl["EPS_Lenkmoment"] * (1 if vl["EPS_VZ_Lenkmoment"] == 0 else -1)
    can_t.append(msg.logMonoTime)
    eps_torque.append(signed)
  can_t = np.array(can_t)
  eps_torque = np.array(eps_torque)

  # 2. carControl: latActive, commanded curvature, requested visualAlert
  cc_t, latActive, curv_cmd, visual_alert = [], [], [], []
  for msg in LogReader(args.route):
    if msg.which() != "carControl":
      continue
    cc = msg.carControl
    cc_t.append(msg.logMonoTime)
    latActive.append(cc.latActive)
    curv_cmd.append(cc.actuators.curvature)
    visual_alert.append(str(cc.hudControl.visualAlert))
  cc_t = np.array(cc_t)

  # 3. carState: vEgo (interp to CAN timestamps)
  cs_t, vEgo = [], []
  for msg in LogReader(args.route):
    if msg.which() != "carState":
      continue
    cs_t.append(msg.logMonoTime)
    vEgo.append(msg.carState.vEgo)
  cs_t = np.array(cs_t); vEgo = np.array(vEgo)

  # 4. onroadEvents and selfdriveState alerts (steerSaturated / steerRequired)
  events = []
  for msg in LogReader(args.route):
    if msg.which() == "onroadEvents":
      for e in msg.onroadEvents:
        if e.name == "steerSaturated":
          events.append((msg.logMonoTime, "steerSaturated", e.enable))
    elif msg.which() == "selfdriveState":
      a = msg.selfdriveState.alertText1
      if "Take Over" in a or "take over" in a.lower():
        events.append((msg.logMonoTime, "alert", a))

  # 5. Simulate carcontroller MEB power loop, aligned to CAN frames at STEER_STEP=2
  power = 0
  bar = np.zeros(len(can_t))
  for i, t in enumerate(can_t):
    # nearest carControl + carState sample
    cc_i = np.searchsorted(cc_t, t) - 1
    cs_i = np.searchsorted(cs_t, t) - 1
    if cc_i < 0 or cs_i < 0:
      continue
    if not latActive[cc_i]:
      power = max(power - STEERING_POWER_STEP, 0) if power > 0 else 0
      bar[i] = power / STEERING_POWER_MAX
      continue
    abs_tq = abs(eps_torque[i])
    target_power_driver = int(np.interp(abs_tq, [args.threshold, STEER_DRIVER_MAX],
                                        [STEERING_POWER_MAX, STEERING_POWER_MIN]))
    target_power = int(np.interp(vEgo[cs_i], [0., 0.5],
                                 [STEERING_POWER_MIN, target_power_driver]))
    min_power = max(power - STEERING_POWER_STEP, STEERING_POWER_MIN)
    max_power = min(power + STEERING_POWER_STEP, STEERING_POWER_MAX)
    power = min(max(target_power, min_power), max_power)
    bar[i] = power / STEERING_POWER_MAX

  full = bar >= 0.99   # bar visually full
  empty = bar <= 0.10  # bar visually empty
  saturated = np.abs(np.array([np.interp(t, cc_t, curv_cmd) for t in can_t])) > SAT_FRAC * CURVATURE_MAX

  def runs(mask):
    out, start = [], None
    for i, m in enumerate(mask):
      if m and start is None: start = i
      elif not m and start is not None:
        out.append((start, i)); start = None
    if start is not None:
      out.append((start, len(mask)))
    return out

  full_runs = runs(full)
  empty_runs = runs(empty)
  sat_runs = runs(saturated)

  print(f"route: {args.route}")
  print(f"frames (CAN LH_EPS_03): {len(can_t)}  threshold (STEER_DRIVER_ALLOWANCE): {args.threshold}")
  print()
  print("=== bar stats ===")
  print(f"  bar full (>=0.99): {full.sum()} frames in {len(full_runs)} runs")
  print(f"  bar empty (<=0.10): {empty.sum()} frames in {len(empty_runs)} runs")
  print(f"  commanded curvature near safety limit (|curv|>{SAT_FRAC}*{CURVATURE_MAX}): {saturated.sum()} frames in {len(sat_runs)} runs")
  print()

  # cross-reference: bar full intervals — did any onroadEvent steerSaturated fire inside them?
  print("=== bar-full intervals vs steerSaturated event ===")
  for s, e in full_runs:
    if e - s < 50:
      continue  # skip <500ms full-bar
    ts, te = can_t[s], can_t[e - 1]
    overlapping = [ev for ev in events if ts - 0.3e9 <= ev[0] <= te + 0.3e9]
    sat_overlap = saturated[s:e].any()
    print(f"  bar full {ts}..{te} ({(te-ts)/1e6:.0f}ms): curv_saturated_overlap={sat_overlap}  events={overlapping[:3]}")
  print()
  print("=== steerSaturated events vs bar state at that time ===")
  for t, name, info in events[:30]:
    if name != "steerSaturated":
      continue
    i = np.searchsorted(can_t, t)
    if 0 <= i < len(bar):
      print(f"  t={t} steerSaturated={info}  bar={bar[i]:.2f}  |curv_cmd|={abs(np.interp(t, cc_t, curv_cmd)):.4f}")


if __name__ == "__main__":
  main()
