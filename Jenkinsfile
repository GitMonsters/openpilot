def retryWithDelay(int maxRetries, int delay, Closure body) {
  for (int i = 0; i < maxRetries; i++) {
    try {
      return body()
    } catch (Exception e) {
      sleep(delay)
    }
  }
  throw Exception("Failed after ${maxRetries} retries")
}

def TIZI_NEEDS_CAN = "tizi-needs-can-tmp"
def TIZI_LSMC = "comma-ff542eb7"

def device(String ip, String step_label, String cmd) {
  withCredentials([file(credentialsId: 'id_rsa', variable: 'key_file')]) {
    def ssh_cmd = """
ssh -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 -o BatchMode=yes -o StrictHostKeyChecking=no -i ${key_file} 'comma@${ip}' exec /usr/bin/bash <<'END'

set -e

export TERM=xterm-256color

shopt -s huponexit # kill all child processes when the shell exits

export CI=1
export PYTHONWARNINGS=error
#export LOGPRINT=debug # this has gotten too spammy...
export TEST_DIR=${env.TEST_DIR}
export SOURCE_DIR=${env.SOURCE_DIR}
export GIT_BRANCH=${env.GIT_BRANCH}
export GIT_COMMIT=${env.GIT_COMMIT}
export CI_ARTIFACTS_TOKEN=${env.CI_ARTIFACTS_TOKEN}
export GITHUB_COMMENTS_TOKEN=${env.GITHUB_COMMENTS_TOKEN}
export AZURE_TOKEN='${env.AZURE_TOKEN}'
# only use 1 thread for tici tests since most require HIL
export PYTEST_ADDOPTS="-n0 -s"


export GIT_SSH_COMMAND="ssh -i /data/gitkey"

source ~/.bash_profile
if [ -f /TICI ]; then
  source /etc/profile

  rm -rf /tmp/tmp*
  rm -rf ~/.commacache
  rm -rf /dev/shm/*
  rm -rf /dev/tmp/tmp*

  if ! systemctl is-active --quiet systemd-resolved; then
    echo "restarting resolved"
    sudo systemctl start systemd-resolved
    sleep 3
  fi

  # restart aux USB
  if [ -e /sys/bus/usb/drivers/hub/3-0:1.0 ]; then
    echo "restarting aux usb"
    echo "3-0:1.0" | sudo tee /sys/bus/usb/drivers/hub/unbind
    sleep 0.5
    echo "3-0:1.0" | sudo tee /sys/bus/usb/drivers/hub/bind
  fi
fi
if [ -f /data/openpilot/launch_env.sh ]; then
  source /data/openpilot/launch_env.sh
fi

ln -snf ${env.TEST_DIR} /data/pythonpath

cd ${env.TEST_DIR} || true
time ${cmd}
END"""

    sh script: ssh_cmd, label: step_label
  }
}

def rebootDevice(String ip) {
  withCredentials([file(credentialsId: 'id_rsa', variable: 'key_file')]) {
    sh returnStatus: true, script: """
ssh -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o BatchMode=yes -o StrictHostKeyChecking=no -i ${key_file} 'comma@${ip}' 'sudo reboot'
"""
  }

  sleep(20)
  retryWithDelay(24, 5) {
    device(ip, "wait for reboot", "true")
  }
}

def deviceStage(String stageName, String deviceType, List extra_env, def steps) {
  stage(stageName) {
    if (currentBuild.result != null) {
        return
    }

    if (isReplay()) {
      error("REPLAYING TESTS IS NOT ALLOWED. FIX THEM INSTEAD.")
    }

    def extra = extra_env.collect { "export ${it}" }.join('\n');
    def branch = env.BRANCH_NAME ?: 'master';
    def gitDiff = sh returnStdout: true, script: 'curl -s -H "Authorization: Bearer ${GITHUB_COMMENTS_TOKEN}" https://api.github.com/repos/commaai/openpilot/compare/master...${GIT_BRANCH} | jq .files[].filename || echo "/"', label: 'Getting changes'

    def lockOptions = deviceType.startsWith("comma-") ?
      [resource: deviceType, inversePrecedence: true, variable: 'device_ip'] :
      [resource: "", label: deviceType, inversePrecedence: true, variable: 'device_ip', quantity: 1, resourceSelectStrategy: 'random']

    lock(lockOptions) {
      docker.image('ghcr.io/commaai/alpine-ssh').inside('--user=root') {
        timeout(time: 35, unit: 'MINUTES') {
          if (deviceType == "tizi-needs-can-tmp") {
            rebootDevice(device_ip)
          }

          retry (3) {
            def date = sh(script: 'date', returnStdout: true).trim();
            device(device_ip, "set time", "date -s '" + date + "'")
            device(device_ip, "git checkout", extra + "\n" + readFile("selfdrive/test/setup_device_ci.sh"))
          }
          steps.each { item ->
            def name = item[0]
            def cmd = item[1]

            def args = item[2]
            def diffPaths = args.diffPaths ?: []
            def cmdTimeout = args.timeout ?: 9999

            if (branch != "master" && !branch.contains("__jenkins_loop_") && diffPaths && !hasPathChanged(gitDiff, diffPaths)) {
              println "Skipping ${name}: no changes in ${diffPaths}."
              return
            } else {
              timeout(time: cmdTimeout, unit: 'SECONDS') {
                device(device_ip, name, cmd)
              }
            }
          }
        }
      }
    }
  }
}

def hasPathChanged(String gitDiff, List<String> paths) {
  for (path in paths) {
    if (gitDiff.contains(path)) {
      return true
    }
  }
  return false
}

def isReplay() {
  def replayClass = "org.jenkinsci.plugins.workflow.cps.replay.ReplayCause"
  return currentBuild.rawBuild.getCauses().any{ cause -> cause.toString().contains(replayClass) }
}

def setupCredentials() {
  withCredentials([
    string(credentialsId: 'azure_token', variable: 'AZURE_TOKEN'),
  ]) {
    env.AZURE_TOKEN = "${AZURE_TOKEN}"
  }

  withCredentials([
    string(credentialsId: 'ci_artifacts_pat', variable: 'CI_ARTIFACTS_TOKEN'),
  ]) {
    env.CI_ARTIFACTS_TOKEN = "${CI_ARTIFACTS_TOKEN}"
  }

  withCredentials([
    string(credentialsId: 'post_comments_github_pat', variable: 'GITHUB_COMMENTS_TOKEN'),
  ]) {
    env.GITHUB_COMMENTS_TOKEN = "${GITHUB_COMMENTS_TOKEN}"
  }
}

def step(String name, String cmd, Map args = [:]) {
  return [name, cmd, args]
}

node {
  env.CI = "1"
  env.PYTHONWARNINGS = "error"
  env.TEST_DIR = "/data/openpilot"
  env.SOURCE_DIR = "/data/openpilot_source/"
  setupCredentials()

  env.GIT_BRANCH = checkout(scm).GIT_BRANCH
  env.GIT_COMMIT = checkout(scm).GIT_COMMIT

  def excludeBranches = ['__nightly', 'devel', 'devel-staging', 'release3', 'release3-staging',
                         'release-tici', 'release-tizi', 'release-tizi-staging', 'release-mici-staging', 'testing-closet*', 'hotfix-*']
  def excludeRegex = excludeBranches.join('|').replaceAll('\\*', '.*')

  if (env.BRANCH_NAME != 'master' && !env.BRANCH_NAME.contains('__jenkins_loop_')) {
    properties([
        disableConcurrentBuilds(abortPrevious: true)
    ])
  }

  if (env.BRANCH_NAME == 'jenkins_mem_probe') {
    deviceStage("ui dma-buf probe", TIZI_NEEDS_CAN, ["UNSAFE=1"], [
      step("install fixed raylib", '''
set -euo pipefail

workdir=/tmp/raylib_dmabuf_fix
rm -rf "${workdir}"
mkdir -p "${workdir}/install" "${workdir}/include"

git clone -b master --no-tags https://github.com/commaai/raylib.git "${workdir}/raylib_repo"
cd "${workdir}/raylib_repo"
git fetch origin faf6e4392780de7471504e43e70975efaba07e95
git reset --hard faf6e4392780de7471504e43e70975efaba07e95
git clean -xdff .
git apply <<'PATCH'
diff --git a/src/platforms/rcore_comma.c b/src/platforms/rcore_comma.c
index 7883c91f..86af8a43 100644
--- a/src/platforms/rcore_comma.c
+++ b/src/platforms/rcore_comma.c
@@ -969,6 +969,9 @@ void SwapScreenBuffer(void) {
   }
 
   platform.gbm.current_bo = platform.gbm.next_bo;
+  platform.gbm.current_fb = platform.gbm.next_fb;
+  platform.gbm.next_bo = NULL;
+  platform.gbm.next_fb = 0;
 }
 
 //----------------------------------------------------------------------------------
@@ -1099,6 +1102,18 @@ void PollInputEvents(void) {
 //----------------------------------------------------------------------------------
 
 int InitPlatform(void) {
+  platform.drm.fd = -1;
+  platform.egl.display = EGL_NO_DISPLAY;
+  platform.egl.surface = EGL_NO_SURFACE;
+  platform.egl.context = EGL_NO_CONTEXT;
+  platform.gbm.device = NULL;
+  platform.gbm.surface = NULL;
+  platform.gbm.current_bo = NULL;
+  platform.gbm.next_bo = NULL;
+  platform.gbm.current_fb = 0;
+  platform.gbm.next_fb = 0;
+  platform.touch.fd = -1;
+
   // only support fullscreen
   CORE.Window.fullscreen = true;
   CORE.Window.flags |= FLAG_FULLSCREEN_MODE;
@@ -1169,8 +1184,21 @@ void ClosePlatform(void) {
     platform.egl.display = EGL_NO_DISPLAY;
   }
 
-  if (platform.gbm.surface && platform.gbm.next_bo) {
-    gbm_surface_release_buffer(platform.gbm.surface, platform.gbm.next_bo);
+  if (platform.gbm.surface) {
+    if (platform.gbm.next_bo) {
+      gbm_surface_release_buffer(platform.gbm.surface, platform.gbm.next_bo);
+      platform.gbm.next_bo = NULL;
+      platform.gbm.next_fb = 0;
+    }
+
+    if (platform.gbm.current_bo) {
+      gbm_surface_release_buffer(platform.gbm.surface, platform.gbm.current_bo);
+      platform.gbm.current_bo = NULL;
+      platform.gbm.current_fb = 0;
+    }
+
+    gbm_surface_destroy(platform.gbm.surface);
+    platform.gbm.surface = NULL;
   }
 
   if (platform.gbm.device) {
@@ -1178,5 +1206,13 @@ void ClosePlatform(void) {
     platform.gbm.device = NULL;
   }
 
-  close(platform.touch.fd);
+  if (platform.drm.fd >= 0) {
+    close(platform.drm.fd);
+    platform.drm.fd = -1;
+  }
+
+  if (platform.touch.fd >= 0) {
+    close(platform.touch.fd);
+    platform.touch.fd = -1;
+  }
 }
PATCH

cd "${workdir}/raylib_repo/src"
make -j$(nproc) PLATFORM=PLATFORM_COMMA RAYLIB_RELEASE_PATH="${workdir}/install"
cp raylib.h raymath.h rlgl.h "${workdir}/include/"
curl -fsSLo "${workdir}/include/raygui.h" https://raw.githubusercontent.com/raysan5/raygui/76b36b597edb70ffaf96f046076adc20d67e7827/src/raygui.h

git clone -b master --no-tags https://github.com/commaai/raylib-python-cffi.git "${workdir}/raylib_python_repo"
cd "${workdir}/raylib_python_repo"
git fetch origin a0710d95af3c12fd7f4b639589be9a13dad93cb6
git reset --hard a0710d95af3c12fd7f4b639589be9a13dad93cb6
git clean -xdff .
RAYLIB_PLATFORM=PLATFORM_COMMA RAYLIB_INCLUDE_PATH="${workdir}/include" RAYLIB_LIB_PATH="${workdir}/install" python setup.py bdist_wheel
python -m pip install --force-reinstall --no-deps dist/raylib-*.whl
python - <<'PY'
import pyray as rl
print("installed raylib:", rl.RAYLIB_VERSION)
PY
''', [timeout: 900]),
      step("ui dma-buf probe", '''
set -euo pipefail

cleanup_ui_probe() {
  pkill -INT -f "ui-dmabuf-probe" || true
  sleep 1
  pkill -KILL -f "ui-dmabuf-probe" || true
}

snapshot_dma() {
  label="$1"
  echo "==== DMABUF ${label} $(date -Is) ===="
  free -m
  du -sh /dev/shm 2>/dev/null || true
  if sudo test -r /sys/kernel/debug/dma_buf/bufinfo; then
    sudo awk '
      function remember_object() {
        if (!in_object) return
        if (mdss) {
          mdss_count += 1
          mdss_total += size
          mdss_by_size[size] += 1
        }
      }
      /^[[:space:]]*[0-9]+[[:space:]]/ {
        remember_object()
        in_object=1
        size=$1 + 0
        exporter=$5
        if (exporter == "") exporter="unknown"
        mdss=0
        total += size
        count += 1
        by_exporter[exporter] += size
        by_size[size] += 1
        next
      }
      /mdss_mdp/ {
        mdss=1
      }
      END {
        remember_object()
        printf("count %d total_mb %.1f\\n", count, total / 1048576);
        for (exporter in by_exporter) printf("exporter %s %.1f MB\\n", exporter, by_exporter[exporter] / 1048576);
        printf("mdss_count %d mdss_total_mb %.1f\\n", mdss_count, mdss_total / 1048576);
        for (size in by_size) printf("size %d count %d total_mb %.1f\\n", size, by_size[size], size * by_size[size] / 1048576);
        for (size in mdss_by_size) printf("mdss_size %d count %d total_mb %.1f\\n", size, mdss_by_size[size], size * mdss_by_size[size] / 1048576);
      }
    ' /sys/kernel/debug/dma_buf/bufinfo
    sudo awk '
      /^[[:space:]]*[0-9]+[[:space:]]/ {obj=$0; show=0; lines=""}
      {lines=lines $0 "\\n"}
      /mdss_mdp/ {show=1}
      /^Total [0-9]+ devices attached/ {
        if (show) printf "%s", lines
        lines=""
      }
    ' /sys/kernel/debug/dma_buf/bufinfo | head -160
  else
    echo "no readable dma_buf/bufinfo"
  fi
}

run_ui_once() {
  mode="$1"
  python - "$mode" <<'PY'
import atexit
import os
import sys
import time

import pyray as rl

from openpilot.system.ui.lib.application import gui_app

mode = sys.argv[1]
gui_app.init_window("ui-dmabuf-probe")
for _ in range(5):
  rl.begin_drawing()
  rl.clear_background(rl.BLACK)
  rl.draw_text("probe", 20, 20, 40, rl.WHITE)
  rl.end_drawing()
  time.sleep(0.05)

if mode == "close":
  gui_app.close()
elif mode == "sys_exit_without_close":
  atexit.unregister(gui_app.close)
  sys.exit(0)
elif mode == "os_exit_without_close":
  os._exit(0)
else:
  raise ValueError(mode)
PY
}

run_case() {
  name="$1"
  mode="$2"
  snapshot_dma "${name}_before"
  for i in 1 2 3; do
    echo "==== UI CASE ${name} ITER ${i} mode=${mode} ===="
    cleanup_ui_probe
    rm -rf /dev/shm/*
    run_ui_once "${mode}"
    cleanup_ui_probe
    sleep 2
    snapshot_dma "${name}_after_${i}"
  done
}

snapshot_dma boot
run_case sys_exit_without_close sys_exit_without_close
run_case os_exit_without_close os_exit_without_close
run_case close_only close
''', [timeout: 600]),
    ])
    return
  }

  if (env.BRANCH_NAME == 'jenkins_mem_probe_onroad') {
    deviceStage("onroad memory probe", TIZI_NEEDS_CAN, ["UNSAFE=1"], [
      step("build openpilot", "cd system/manager && ./build.py"),
      step("memory probe", '''
set +e

cleanup_onroad() {
  pkill -INT -f "/usr/local/venv/bin/pytest" || true
  pkill -INT -f "system/manager/manager.py" || true
  sudo pkill -INT -x camerad || true
  sleep 3
  pkill -KILL -f "/usr/local/venv/bin/pytest" || true
  pkill -KILL -f "system/manager/manager.py" || true
  sudo pkill -KILL -x camerad || true
}

snapshot_memory() {
  label="$1"
  echo "==== MEMSNAP ${label} $(date -Is) ===="
  free -m
  awk '/MemTotal|MemFree|MemAvailable|Buffers|Cached|SReclaimable|SUnreclaim|Slab|Shmem|Cma/ {print}' /proc/meminfo
  echo "-- /dev/shm"
  du -sh /dev/shm 2>/dev/null || true
  echo "-- largest processes"
  ps -eo pid,ppid,rss,vsz,comm,args --sort=-rss | head -30
  echo "-- dma-buf summary"
  if sudo test -r /sys/kernel/debug/dma_buf/bufinfo; then
    sudo awk '
      /^[[:space:]]*[0-9]+[[:space:]]/ {
        size=$1; exporter=$5; if (exporter == "") exporter="unknown";
        total += size; count += 1; by_exporter[exporter] += size;
      }
      END {
        printf("count %d total_mb %.1f\\n", count, total / 1048576);
        for (exporter in by_exporter) printf("exporter %s %.1f MB\\n", exporter, by_exporter[exporter] / 1048576);
      }
    ' /sys/kernel/debug/dma_buf/bufinfo
    sudo head -120 /sys/kernel/debug/dma_buf/bufinfo
  else
    echo "no readable dma_buf/bufinfo"
  fi
  echo "-- ion heaps"
  sudo sh -c 'for f in /sys/kernel/debug/ion/heaps/* /d/ion/heaps/*; do [ -r "$f" ] && echo "### $f" && head -160 "$f"; done' || true
  echo "-- kgsl"
  sudo sh -c 'for f in /sys/class/kgsl/kgsl-3d0/gpubusy /sys/class/kgsl/kgsl-3d0/gpu_model /sys/kernel/debug/kgsl/proc/*/mem /d/kgsl/proc/*/mem; do [ -r "$f" ] && echo "### $f" && head -120 "$f"; done' || true
}

fail=0
snapshot_memory boot
for i in 1 2 3 4 5; do
  echo "==== ONROAD RUN ${i} ===="
  rm -rf /data/media/0/realdata/*
  sync
  echo 3 | sudo tee /proc/sys/vm/drop_caches || true
  snapshot_memory before_${i}
  pytest selfdrive/test/test_onroad.py -s
  rc=$?
  echo "PYTEST_RC ${i} ${rc}"
  if [ "${rc}" -ne 0 ] && [ "${fail}" -eq 0 ]; then
    fail="${rc}"
  fi
  cleanup_onroad
  sleep 8
  snapshot_memory after_${i}
done

exit "${fail}"
''', [timeout: 1800]),
    ])
    return
  }

  try {
    if (env.BRANCH_NAME == 'devel-staging') {
      deviceStage("build release-tizi-staging", TIZI_NEEDS_CAN, [], [
        step("build release-tizi-staging", "RELEASE_BRANCH=release-tizi-staging $SOURCE_DIR/release/build_release.sh && git push -f origin release-tizi-staging:release-mici-staging"),
      ])
    }

    if (env.BRANCH_NAME == '__nightly') {
      parallel (
        'nightly': {
          deviceStage("build nightly", TIZI_NEEDS_CAN, [], [
            step("build nightly", "RELEASE_BRANCH=nightly $SOURCE_DIR/release/build_release.sh"),
          ])
        },
        'nightly-dev': {
          deviceStage("build nightly-dev", TIZI_NEEDS_CAN, [], [
            step("build nightly-dev", "PANDA_DEBUG_BUILD=1 RELEASE_BRANCH=nightly-dev $SOURCE_DIR/release/build_release.sh"),
          ])
        },
      )
    }

    if (!env.BRANCH_NAME.matches(excludeRegex)) {
    parallel (
      'onroad tests': {
        deviceStage("onroad", TIZI_NEEDS_CAN, ["UNSAFE=1"], [
          step("build openpilot", "cd system/manager && ./build.py"),
          step("check dirty", "release/check-dirty.sh"),
          step("onroad tests", "pytest selfdrive/test/test_onroad.py -s", [timeout: 60]),
        ])
      },
      'HW + Unit Tests': {
        deviceStage("tizi-hardware", "tizi-common", ["UNSAFE=1"], [
          step("build", "cd system/manager && ./build.py"),
          step("test power draw", "pytest -s system/hardware/tici/tests/test_power_draw.py"),
          step("test encoder", "LD_LIBRARY_PATH=/usr/local/lib pytest system/loggerd/tests/test_encoder.py", [diffPaths: ["system/loggerd/"]]),
          step("test manager", "pytest system/manager/test/test_manager.py"),
        ])
      },
      'camerad OX03C10': {
        deviceStage("OX03C10", "tizi-ox03c10", ["UNSAFE=1"], [
          step("build", "cd system/manager && ./build.py"),
          step("test pandad", "pytest selfdrive/pandad/tests/test_pandad.py"),
          step("test camerad", "pytest system/camerad/test/test_camerad.py", [timeout: 90]),
        ])
      },
      'camerad OS04C10': {
        deviceStage("OS04C10", "tici-os04c10", ["UNSAFE=1"], [
          step("build", "cd system/manager && ./build.py"),
          step("test pandad", "pytest selfdrive/pandad/tests/test_pandad.py"),
          step("test camerad", "pytest system/camerad/test/test_camerad.py", [timeout: 90]),
        ])
      },
      'sensord': {
        deviceStage("LSM + MMC", TIZI_LSMC, ["UNSAFE=1"], [
          step("build", "cd system/manager && ./build.py"),
          step("test sensord", "pytest system/sensord/tests/test_sensord.py"),
        ])
      },
      'replay': {
        deviceStage("model-replay", "tizi-replay", ["UNSAFE=1"], [
          step("build", "cd system/manager && ./build.py", [diffPaths: ["selfdrive/modeld/", "tinygrad_repo", "selfdrive/test/process_replay/model_replay.py"]]),
          step("model replay", "selfdrive/test/process_replay/model_replay.py", [diffPaths: ["selfdrive/modeld/", "tinygrad_repo", "selfdrive/test/process_replay/model_replay.py"]]),
        ])
      },
      'tizi': {
        deviceStage("tizi", "tizi", ["UNSAFE=1"], [
          step("build openpilot", "cd system/manager && ./build.py"),
          step("test pandad loopback", "pytest selfdrive/pandad/tests/test_pandad_loopback.py"),
          step("test pandad spi", "pytest selfdrive/pandad/tests/test_pandad_spi.py"),
          step("test amp", "pytest system/hardware/tici/tests/test_amplifier.py"),
          step("test qcomgpsd", "pytest system/qcomgpsd/tests/test_qcomgpsd.py", [diffPaths: ["system/qcomgpsd/"]]),
        ])
      },

    )
    }
  } catch (Exception e) {
    currentBuild.result = 'FAILED'
    throw e
  }
}
