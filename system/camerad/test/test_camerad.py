import os
import time
import pytest
import numpy as np

from cereal.services import SERVICE_LIST
from openpilot.tools.lib.log_time_series import msgs_to_time_series
from openpilot.system.camerad.snapshot import get_snapshots
from openpilot.selfdrive.test.helpers import collect_logs, log_collector, processes_context

TEST_TIMESPAN = 10
CAMERAS = ('roadCameraState', 'driverCameraState', 'wideRoadCameraState')
EXPOSURE_STABLE_COUNT = 3
EXPOSURE_RANGE = (0.15, 0.35)
SYNC_STARTUP_SKIP = 5
MAX_TEST_TIME = 25
DRIVER_STAGGER_RANGE_MS = (20, 30)
SYNCED_OFFSET_TOLERANCE_MS = 1.1
OS04_DRIVER_STAGGER_MS = 25.0


def _numpy_rgb2gray(im):
  return np.clip(im[:,:,2] * 0.114 + im[:,:,1] * 0.587 + im[:,:,0] * 0.299, 0, 255).astype(np.uint8)

def _exposure_stats(im):
  h, w = im.shape[:2]
  gray = _numpy_rgb2gray(im[h//10:9*h//10, w//10:9*w//10])
  return float(np.median(gray) / 255.), float(np.mean(gray) / 255.)

def _in_range(median, mean):
  lo, hi = EXPOSURE_RANGE
  return lo < median < hi and lo < mean < hi

def _exposure_stable(results):
  return all(
    len(v) >= EXPOSURE_STABLE_COUNT and all(_in_range(*s) for s in v[-EXPOSURE_STABLE_COUNT:])
    for v in results.values()
  )

def _first_stable_sample(checks):
  for i in range(EXPOSURE_STABLE_COUNT - 1, len(checks)):
    start = i + 1 - EXPOSURE_STABLE_COUNT
    if all(_in_range(*s) for s in checks[start:i+1]):
      return i
  return None


def run_and_log(procs, services, duration):
  with processes_context(procs):
    return collect_logs(services, duration)

@pytest.fixture(scope="module")
def _camera_session():
  """Single camerad session that collects logs and exposure data.
     Runs until exposure stabilizes (min TEST_TIMESPAN seconds for enough log data)."""
  with processes_context(["camerad"]), log_collector(CAMERAS) as (raw_logs, lock):
    exposure = {cam: [] for cam in CAMERAS}
    start = time.monotonic()
    while time.monotonic() - start < MAX_TEST_TIME:
      rpic, dpic = get_snapshots(frame="roadCameraState", front_frame="driverCameraState")
      wpic, _ = get_snapshots(frame="wideRoadCameraState")
      for cam, img in zip(CAMERAS, [rpic, dpic, wpic], strict=True):
        exposure[cam].append(_exposure_stats(img))

      if time.monotonic() - start >= TEST_TIMESPAN and _exposure_stable(exposure):
        break

  with lock:
    ts = msgs_to_time_series(raw_logs)

  for cam in CAMERAS:
    cnt = len(ts[cam]['t'])
    assert cnt >= SERVICE_LIST[cam].frequency * TEST_TIMESPAN * 0.5, f"not enough frames {cam}: got {cnt}"

    log_span = ts[cam]['t'][-1] - ts[cam]['t'][0]
    expected_frames = SERVICE_LIST[cam].frequency * log_span + 1
    assert expected_frames*0.8 < cnt < expected_frames*1.2, f"unexpected frame count {cam}: {expected_frames=}, got {cnt}"

    dts = np.abs(np.diff(ts[cam]['timestampSof']/1e6) - 1000/SERVICE_LIST[cam].frequency)
    assert (dts < 1.0).all(), f"{cam} dts(ms) out of spec: max diff {dts.max()}, 99 percentile {np.percentile(dts, 99)}"

  return ts, exposure

@pytest.fixture(scope="module")
def logs(_camera_session):
  return _camera_session[0]

@pytest.fixture(scope="module")
def exposure_data(_camera_session):
  return _camera_session[1]

@pytest.mark.tici
class TestCamerad:
  @pytest.mark.parametrize("cam", CAMERAS)
  def test_camera_exposure(self, exposure_data, cam):
    lo, hi = EXPOSURE_RANGE
    checks = exposure_data[cam]
    assert len(checks) >= EXPOSURE_STABLE_COUNT, f"{cam}: only got {len(checks)} samples"

    # check that exposure converges into the valid range
    converged_at = _first_stable_sample(checks)
    assert converged_at is not None, \
      f"{cam}: exposure never stabilized over {EXPOSURE_STABLE_COUNT} consecutive samples. " + \
      " | ".join(f"#{i+1}: med={m:.4f} mean={u:.4f}" for i, (m, u) in enumerate(checks))

    # check that exposure is stable once converged (no regressions)
    for i, (median, mean) in enumerate(checks[converged_at + 1:], start=converged_at + 1):
      ok = _in_range(median, mean)
      if not ok:
        pytest.fail(f"{cam}: exposure regressed on sample {i+1} " +
                    f"(median={median:.4f}, mean={mean:.4f}, expected: ({lo}, {hi}))")

  def test_frame_skips(self, logs):
    for c in CAMERAS:
      assert set(np.diff(logs[c]['frameId'])) == {1, }, f"{c} has frame skips"

  def test_frame_sync(self, logs):
    SYNCED_CAMS = ('roadCameraState', 'wideRoadCameraState')
    n = range(SYNC_STARTUP_SKIP, len(logs['roadCameraState']['t'][:-10]))

    frame_ids = {i: [logs[cam]['frameId'][i] for cam in CAMERAS] for i in n}
    assert all(len(set(v)) == 1 for v in frame_ids.values()), "frame IDs not aligned"

    # road and wide cameras should be synced within 1.1ms
    synced_times = {i: [logs[cam]['timestampSof'][i] for cam in SYNCED_CAMS] for i in n}
    diffs = {i: (max(ts) - min(ts))/1e6 for i, ts in synced_times.items()}
    laggy_frames = {k: v for k, v in diffs.items() if v > 1.1}
    assert len(laggy_frames) == 0, f"Frames not synced properly: {laggy_frames=}"

    driver_sensor = set(logs['driverCameraState']['sensor'])
    driver_expected_offsets_ms = (
      (OS04_DRIVER_STAGGER_MS, 1000.0 / SERVICE_LIST['driverCameraState'].frequency)
      if driver_sensor == {'os04c10'} else None
    )

    # OX03 driver camera should be staggered ~25ms from road camera. OS04 driver
    # frames can align at either the staggered phase or one 20Hz period offset
    # while road/wide remain tightly synced.
    for i in n:
      offset_ms = abs(logs['driverCameraState']['timestampSof'][i] - logs['roadCameraState']['timestampSof'][i]) / 1e6
      if driver_expected_offsets_ms is None:
        lo, hi = DRIVER_STAGGER_RANGE_MS
        assert lo < offset_ms < hi, f"driver camera stagger out of range at frame {i}: {offset_ms:.1f}ms (expected ~25ms)"
      else:
        assert any(abs(offset_ms - expected) < SYNCED_OFFSET_TOLERANCE_MS for expected in driver_expected_offsets_ms), \
          f"driver camera offset out of range at frame {i}: {offset_ms:.1f}ms (expected one of {driver_expected_offsets_ms})"

  def test_sanity_checks(self, logs):
    self._sanity_checks(logs)

  def _sanity_checks(self, ts):
    for c in CAMERAS:
      assert c in ts
      assert len(ts[c]['t']) > 20

      # not a valid request id
      assert 0 not in ts[c]['requestId']

      # should monotonically increase
      assert np.all(np.diff(ts[c]['frameId']) >= 1)
      assert np.all(np.diff(ts[c]['requestId']) >= 1)

      # EOF > SOF
      assert np.all((ts[c]['timestampEof'] - ts[c]['timestampSof']) > 0)

      # logMonoTime > SOF
      assert np.all((ts[c]['t'] - ts[c]['timestampSof']/1e9) > 1e-7)

      # logMonoTime > EOF, needs some tolerance since EOF is (SOF + readout time) but there is noise in the SOF timestamping (done via IRQ)
      assert np.mean((ts[c]['t'] - ts[c]['timestampEof']/1e9) > 1e-7) > 0.7  # should be mostly logMonoTime > EOF
      assert np.all((ts[c]['t'] - ts[c]['timestampEof']/1e9) > -0.10)        # when EOF > logMonoTime, it should never be more than two frames

  def test_stress_test(self):
    os.environ['SPECTRA_ERROR_PROB'] = '0.008'
    try:
      logs = run_and_log(["camerad", ], CAMERAS, 10)
    finally:
      del os.environ['SPECTRA_ERROR_PROB']
    ts = msgs_to_time_series(logs)

    # we should see some jumps from introduced errors
    assert np.max([ np.max(np.diff(ts[c]['frameId'])) for c in CAMERAS ]) > 1
    assert np.max([ np.max(np.diff(ts[c]['requestId'])) for c in CAMERAS ]) > 1

    self._sanity_checks(ts)
