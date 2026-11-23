#!/bin/sh
# Offscreen render of the 9spokes.warp bar widget on an Omarchy machine.
# Needs no compositor, no root and no WARP install: it runs Quickshell with
# QT_QPA_PLATFORM=offscreen against a copy of Omarchy's shell (for qs.Commons
# and qs.Ui), a stub KeyboardPanel (offscreen has no PanelWindow backend), a
# throwaway HOME whose current theme is <theme>, and fake-warp-cli.
#
# usage: tests/warp-harness/run.sh <out-dir> <theme> <state[,state...]>
# states: connected connecting disconnected unregistered unable daemon-down,
#         or keep-<name> to snapshot without changing state (for IPC tests:
#         qs ipc -p "$work/harness/shell.qml" call 9spokes.warp disconnect).
set -eu
out=$(realpath "$1"); theme=$2; seq=$3
here=$(dirname "$(realpath "$0")")
repo=$(realpath "$here/../..")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cp -r /usr/share/omarchy/shell "$work/harness"
cp "$here/shell.qml" "$work/harness/shell.qml"
cp "$here/KeyboardPanel.qml" "$work/harness/Ui/KeyboardPanel.qml"
cp -r "$repo/warp/plugin" "$work/harness/warpplugin"
mkdir -p "$work/home/.local/state/omarchy/current" "$out"
ln -s "/usr/share/omarchy/themes/$theme" "$work/home/.local/state/omarchy/current/theme"

HOME=$work/home LANG=C.UTF-8 QT_QPA_PLATFORM=offscreen \
  FAKE_WARP_STATE=$work/state FAKE_WARP_CLI=$here/fake-warp-cli \
  WARP_SHOTS=$out WARP_THEME=$theme WARP_SEQUENCE=$seq \
  timeout 120 qs -p "$work/harness/shell.qml" 2>&1 | grep -E 'WARPHARNESS|ERROR|TypeError|ReferenceError' || true
echo "warp-cli calls:"; sort "$work/state.log" | uniq -c
