#!/bin/sh
set -eu
Xvfb "${DISPLAY:-:99}" -screen 0 1024x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1
openbox >/tmp/openbox.log 2>&1 &
exec tail -f /dev/null
