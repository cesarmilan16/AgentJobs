#!/bin/sh
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
XVFB_PID=$!

# Esperar a que el socket esté disponible (máx 10 s)
i=0
while [ $i -lt 20 ]; do
    [ -S /tmp/.X11-unix/X99 ] && break
    sleep 0.5
    i=$((i+1))
done

export DISPLAY=:99
python -m job_agent "$@"
RETVAL=$?

kill "$XVFB_PID" 2>/dev/null || true
exit $RETVAL
