#!/usr/bin/env bash
# upload.sh — push python/main.py to the Pico, working around the FM console.
#
# Why this is not a plain `mpremote cp`:
#   main.py boots into the interactive FM console, which sits in input()
#   and catches Ctrl-C by design.  mpremote needs the plain MicroPython
#   REPL to upload files, so `mpremote cp` fails with
#   "could not enter raw repl" (the console swallows the handshake).
#
# This script:
#   1. opens the USB CDC port (DTR assert lets main.py finish waiting for a
#      terminal), waits for the `fm>` prompt and types `exit` so the console
#      returns to the plain REPL;
#   2. uploads main.py with `mpremote resume fs cp` (resume = no soft reset,
#      so the console does not grab the REPL again);
#   3. resets the board with machine.reset() so the new main.py runs.
#
# Works in all states: console up, plain REPL, or no main.py yet (first boot).
#
# Usage: ./upload.sh [path-to-main.py]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-$(dirname "${SCRIPT_DIR}")/python/main.py}"

command -v mpremote >/dev/null 2>&1 || { echo "error: mpremote not found (pip install mpremote)"; exit 1; }
[ -f "${SRC}" ] || { echo "error: not found: ${SRC}"; exit 1; }

# Use the same Python interpreter mpremote runs on (it is guaranteed to have
# pyserial, which the console-exit step needs).
MPY="$(head -1 "$(command -v mpremote)" | sed 's/^#!//')"

# 1. Ask the running console to exit back to the plain REPL.
echo "==> asking the FM console to exit (it holds the REPL)..."
"${MPY}" - <<'EOF'
import glob, sys, time
import serial

port = None
for p in sorted(glob.glob('/dev/ttyACM*')) + sorted(glob.glob('/dev/ttyUSB*')):
    try:
        s = serial.Serial(p, 115200, timeout=0.2)
        port = p
        break
    except Exception:
        continue

if port is None:
    sys.exit("error: no Pico serial port found (/dev/ttyACM*) - is it plugged in?")

print("   port: %s" % port)
buf = b""
sent = False
deadline = time.time() + 10
while time.time() < deadline:
    b = s.read(256)
    if b:
        buf += b
    if not sent and b"fm>" in buf:
        s.write(b"exit\r\n")
        sent = True
        print("   console seen, sending 'exit'")
    if sent and b">>>" in buf:
        print("   console exited, back at the plain REPL")
        break
else:
    # No console prompt: the board is already at the plain REPL (or main.py
    # is absent / console already stopped).  Harmless - proceed to upload.
    print("   (no console prompt; continuing)")
s.close()
EOF

# 2. Upload without a soft reset (the board is at the plain REPL now).
echo "==> uploading $(basename "${SRC}") to :main.py"
mpremote resume fs cp "${SRC}" :main.py

# 3. Reset so the new main.py runs (the USB port may vanish mid-reset).
echo "==> resetting the board..."
mpremote resume exec "import machine; machine.reset()" || true

echo "==> done - the new main.py is running. Open a serial terminal (115200)."
