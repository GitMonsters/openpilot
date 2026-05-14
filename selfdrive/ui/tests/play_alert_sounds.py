#!/usr/bin/env python3
# Push to the comma device. Imports the REAL deployed soundd.py and calls its
# actual get_sound_data() — so whatever wrap/loop bug (or fix) is in the
# deployed file is what you'll hear. No msgq / cereal.messaging required.
#
#   scp selfdrive/ui/tests/play_alert_sounds.py comma@<device>:/data/openpilot/
#   ssh comma@<device>
#   tmux kill-session -t comma                       # stop the openpilot stack
#   cd /data/openpilot
#   python3 selfdrive/ui/tests/play_alert_sounds.py promptDistracted   # best wrap test
#   python3 selfdrive/ui/tests/play_alert_sounds.py warningSoft
#   python3 selfdrive/ui/tests/play_alert_sounds.py laneChange
#   python3 selfdrive/ui/tests/play_alert_sounds.py                    # cycle all

import sys
import time
import types

# --- mock msgq + cereal.messaging so soundd.py imports without the compiled
#     ipc_pyx.so. We never call SubMaster/PubMaster here — we just need the
#     module to load so we can use Soundd.load_sounds + get_sound_data. ---
_fake = types.ModuleType("_fake_messaging")
_fake.SubMaster = _fake.PubMaster = _fake.new_message = lambda *a, **k: None
_fake.fake_event_handle = _fake.drain_sock_raw = lambda *a, **k: None
_fake.MultiplePublishersError = _fake.IpcError = type("E", (), {})

for n in ("msgq", "msgq.ipc_pyx", "cereal.messaging"):
  sys.modules[n] = _fake

# Now safe to import the deployed soundd
sys.path.insert(0, "/data/openpilot")
sys.path.insert(0, ".")

import numpy as np
import sounddevice as sd
from cereal import car
from openpilot.selfdrive.ui.soundd import Soundd, sound_list, SAMPLE_RATE, SAMPLE_BUFFER

AA = car.CarControl.HUDControl.AudibleAlert
NAME = {int(v): k for k, v in AA.schema.enumerants.items()}

ALL_NAMES = [NAME[int(a)] for a in sound_list]


def main():
  args = sys.argv[1:]
  seconds: float | None = 5.0
  if "--seconds" in args:
    i = args.index("--seconds")
    seconds = float(args[i + 1])
    args = args[:i] + args[i + 2:]

  names = args or ALL_NAMES
  if len(names) == 1 and "--seconds" not in sys.argv:
    seconds = None  # single sound → loop forever

  for n in names:
    if not hasattr(AA, n):
      print(f"!! unknown sound: {n}")
      return

  # Instantiate the deployed Soundd. This calls load_sounds() — the same path
  # production uses, asserting mono/16bit/48k.
  s = Soundd()
  s.current_volume = 1.0  # bypass dynamic SPL scaling

  def callback(outdata, frames, _t, _status):
    outdata[:frames, 0] = s.get_sound_data(frames)

  print(f"opening stream  rate={SAMPLE_RATE}  block={SAMPLE_BUFFER}")
  with sd.OutputStream(channels=1, samplerate=SAMPLE_RATE, dtype="float32",
                       blocksize=SAMPLE_BUFFER, callback=callback):
    try:
      for n in names:
        alert = getattr(AA, n)
        # Force every sound to loop indefinitely so the wrap path is exercised
        # repeatedly — even for play_count=1 sounds. We do this by patching the
        # play_count in sound_list, since get_sound_data reads it each call.
        fname, _orig_pc, vol = sound_list[alert]
        sound_list[alert] = (fname, None, vol)
        s.current_alert = alert
        s.current_sound_frame = 0
        forever = seconds is None
        print(f"  → {NAME[int(alert)]} (forced loop)" +
              ("  Ctrl-C to stop" if forever else f"  ({seconds:.0f}s)"))
        end = None if forever else time.monotonic() + seconds
        while forever or time.monotonic() < end:
          time.sleep(0.1)
        # restore and silence between sounds
        sound_list[alert] = (fname, _orig_pc, vol)
        s.current_alert = AA.none
        time.sleep(0.5)
    except KeyboardInterrupt:
      s.current_alert = AA.none
      print("\nstopped")


if __name__ == "__main__":
  main()
