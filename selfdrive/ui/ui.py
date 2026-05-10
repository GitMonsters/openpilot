#!/usr/bin/env python3
import os
import time

from cereal import messaging
from openpilot.system.hardware import TICI
from openpilot.common.realtime import Priority, config_realtime_process, set_core_affinity
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import ui_state

BIG_UI = gui_app.big_ui()


def main():
  cores = {5, }
  # above plannerd and radard
  config_realtime_process(0, Priority.CTRL_HIGH)

  main_layout = None
  render = None
  try:
    gui_app.init_window("UI")
    if BIG_UI:
      main_layout = MainLayout()
    else:
      main_layout = MiciMainLayout()

    pm = messaging.PubMaster(['uiDebug'])
    render = gui_app.render()
    for should_render, frame_time, cpu_time in render:
      extra_start = time.monotonic()
      ui_state.update()

      if should_render:
        # reaffine after power save offlines our core
        if TICI and os.sched_getaffinity(0) != cores:
          try:
            set_core_affinity(list(cores))
          except OSError:
            pass

        extra_cpu = time.monotonic() - extra_start
        msg = messaging.new_message('uiDebug')
        msg.uiDebug.cpuTimeMillis = (cpu_time + extra_cpu) * 1000
        msg.uiDebug.frameTimeMillis = frame_time * 1000
        pm.send('uiDebug', msg)
  finally:
    if render is not None:
      render.close()
    if main_layout is not None:
      main_layout.close()
    gui_app.close()


if __name__ == "__main__":
  main()
