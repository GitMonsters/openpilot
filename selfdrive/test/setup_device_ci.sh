#!/usr/bin/env bash

set -e
set -x

if [ -z "$SOURCE_DIR" ]; then
  echo "SOURCE_DIR must be set"
  exit 1
fi

if [ -z "$GIT_COMMIT" ]; then
  echo "GIT_COMMIT must be set"
  exit 1
fi

if [ -z "$TEST_DIR" ]; then
  echo "TEST_DIR must be set"
  exit 1
fi

shrink_ion_system_heap() {
  local shrink_file="/sys/kernel/debug/ion/heaps/system_shrink"
  local pages

  if [ ! -e "$shrink_file" ]; then
    return
  fi

  pages="$(sudo cat "$shrink_file" 2>/dev/null || true)"
  if [[ "$pages" =~ ^[0-9]+$ ]] && [ "$pages" -gt 0 ]; then
    echo "shrinking ION system heap by $pages pages"
    echo "$pages" | sudo tee "$shrink_file" >/dev/null || true
  fi
}

exclude_ci_launch_env() {
  grep -qxF "/.ci_launch_env.sh" "$TEST_DIR/.git/info/exclude" || echo "/.ci_launch_env.sh" >> "$TEST_DIR/.git/info/exclude"
}

reset_ci_launch_env() {
  exclude_ci_launch_env
  rm -f "$TEST_DIR/.ci_launch_env.sh"
}

setup_vipc_buffer_count() {
  if [ -z "${VIPC_BUFFER_COUNT:-}" ]; then
    return
  fi

  exclude_ci_launch_env
  printf "export VIPC_BUFFER_COUNT=%q\n" "$VIPC_BUFFER_COUNT" >> "$TEST_DIR/.ci_launch_env.sh"
}

# prevent storage from filling up
rm -rf /data/media/0/realdata/*

# aborted Jenkins jobs can leave hardware tests running after the lock is released
pkill -INT -f "/usr/local/venv/bin/pytest" || true
pkill -INT -f "system/manager/manager.py" || true
sudo pkill -INT -x camerad || true
sleep 1
pkill -KILL -f "/usr/local/venv/bin/pytest" || true
pkill -KILL -f "system/manager/manager.py" || true
sudo pkill -KILL -x camerad || true
shrink_ion_system_heap

rm -rf /data/safe_staging/ || true
if [ -d /data/safe_staging/ ]; then
  sudo umount /data/safe_staging/merged/ || true
  rm -rf /data/safe_staging/ || true
fi

CONTINUE_PATH="/data/continue.sh"
tee $CONTINUE_PATH << EOF
#!/usr/bin/env bash

sudo abctl --set_success

# patch sshd config
sudo mount -o rw,remount /
sudo sed -i "s,/data/params/d/GithubSshKeys,/usr/comma/setup_keys," /etc/ssh/sshd_config
sudo systemctl daemon-reload
sudo systemctl restart ssh
sudo systemctl restart NetworkManager
sudo systemctl disable ssh-param-watcher.path
sudo systemctl disable ssh-param-watcher.service
sudo mount -o ro,remount /
sudo systemctl stop power_monitor

while true; do
  if ! sudo systemctl is-active -q ssh; then
    sudo systemctl start ssh
  fi

  #if ! pgrep -f 'ciui.py' > /dev/null 2>&1; then
  #  echo 'starting UI'
  #  cp $SOURCE_DIR/selfdrive/test/ciui.py /data/
  #  /data/ciui.py &
  #fi

  sleep 5s
done

sleep infinity
EOF
chmod +x $CONTINUE_PATH

safe_checkout() {
  # completely clean TEST_DIR

  cd $SOURCE_DIR

  # cleanup orphaned locks
  find .git -type f -name "*.lock" -exec rm {} +

  git reset --hard
  git fetch --no-tags --no-recurse-submodules -j4 --verbose --depth 1 origin $GIT_COMMIT
  find . -maxdepth 1 -not -path './.git' -not -name '.' -not -name '..' -exec rm -rf '{}' \;
  git reset --hard $GIT_COMMIT
  git checkout $GIT_COMMIT
  git clean -xdff
  git submodule sync
  git submodule foreach --recursive "git reset --hard && git clean -xdff"
  git submodule update --init --recursive
  git submodule foreach --recursive "git reset --hard && git clean -xdff"

  git lfs pull
  (ulimit -n 65535 && git lfs prune)

  echo "git checkout done, t=$SECONDS"
  du -hs $SOURCE_DIR $SOURCE_DIR/.git

  rsync -a --delete $SOURCE_DIR $TEST_DIR
}

unsafe_checkout() {( set -e
  # checkout directly in test dir, leave old build products

  cd $TEST_DIR

  # cleanup orphaned locks
  find .git -type f -name "*.lock" -exec rm {} +

  git fetch --no-tags --no-recurse-submodules -j8 --verbose --depth 1 origin $GIT_COMMIT
  git checkout --force --no-recurse-submodules $GIT_COMMIT
  git reset --hard $GIT_COMMIT
  git clean -dff
  git submodule sync
  git submodule foreach --recursive "git reset --hard && git clean -df"
  git submodule update --init --recursive
  git submodule foreach --recursive "git reset --hard && git clean -df"

  git lfs pull
  (ulimit -n 65535 && git lfs prune)
)}

setup_fixed_raylib() {
  if [ -z "${USE_FIXED_RAYLIB:-}" ] || [ ! -f /TICI ]; then
    return
  fi

  local workdir="/data/raylib_dmabuf_fix"
  local raylib_commit="0790ec78f84cc06a44c70ed00bc6de372dbc49f3"
  local bindings_commit="a0710d95af3c12fd7f4b639589be9a13dad93cb6"
  local stamp="${raylib_commit}_${bindings_commit}"

  if [ ! -f "${workdir}/stamp" ] || [ "$(cat "${workdir}/stamp")" != "$stamp" ]; then
    rm -rf /tmp/raylib_dmabuf_fix "$workdir"
    mkdir -p "${workdir}/install" "${workdir}/include"

    git init "${workdir}/raylib_repo"
    (
      cd "${workdir}/raylib_repo"
      git remote add origin https://github.com/commaai/raylib.git
      git fetch --depth 1 origin fix-comma-gbm-teardown
      git checkout --detach "$raylib_commit"
      git clean -xdff .
    )

    (
      cd "${workdir}/raylib_repo/src"
      make -j$(nproc) PLATFORM=PLATFORM_COMMA RAYLIB_RELEASE_PATH="${workdir}/install"
      cp raylib.h raymath.h rlgl.h "${workdir}/include/"
    )
    curl -fsSLo "${workdir}/include/raygui.h" https://raw.githubusercontent.com/raysan5/raygui/76b36b597edb70ffaf96f046076adc20d67e7827/src/raygui.h

    git init "${workdir}/raylib_python_repo"
    (
      cd "${workdir}/raylib_python_repo"
      git remote add origin https://github.com/commaai/raylib-python-cffi.git
      git fetch --depth 1 origin "$bindings_commit"
      git checkout --detach "$bindings_commit"
      git clean -xdff .
      PYTHONWARNINGS=default RAYLIB_PLATFORM=PLATFORM_COMMA RAYLIB_INCLUDE_PATH="${workdir}/include" RAYLIB_LIB_PATH="${workdir}/install" python setup.py bdist_wheel
      rm -rf "${workdir}/site"
      mkdir -p "${workdir}/site"
      python -m zipfile -e dist/raylib-*.whl "${workdir}/site"
    )

    echo "$stamp" > "${workdir}/stamp"
  fi

  exclude_ci_launch_env
  cat > "$TEST_DIR/.ci_launch_env.sh" <<'EOF'
export PYTHONPATH="/data/raylib_dmabuf_fix/site:${PYTHONPATH:-}"
EOF

  (
    cd "$TEST_DIR"
    PYTHONPATH="${workdir}/site:${PYTHONPATH:-}" python - <<'PY'
import pyray as rl
print("fixed raylib:", rl.RAYLIB_VERSION)
PY
  )
}

export GIT_PACK_THREADS=8

# set up environment
if [ ! -d "$SOURCE_DIR" ]; then
  git clone https://github.com/commaai/openpilot.git $SOURCE_DIR
fi

if [ ! -z "$UNSAFE" ]; then
  echo "trying unsafe checkout"
  set +e
  unsafe_checkout
  if [[ "$?" -ne 0 ]]; then
    safe_checkout
  fi
  set -e
else
  echo "doing safe checkout"
  safe_checkout
fi

echo "$TEST_DIR synced with $GIT_COMMIT, t=$SECONDS"
reset_ci_launch_env
setup_fixed_raylib
setup_vipc_buffer_count
