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
#   1. opens the USB CDC port, probes the REPL state by sending CR (Enter)
#      and watching for the prompt:
#        "fm> "  -> the FM console is up: send `exit` (retried) so it
#                   returns to the plain MicroPython REPL;
#        ">>> "  -> already at the plain REPL: nothing to do;
#      a leftover raw-REPL state (from a previous mpremote session) is
#      exited with Ctrl-B first;
#   2. uploads main.py with `mpremote resume fs cp` (resume = no soft reset,
#      so the console does not grab the REPL again);
#   3. resets the board with machine.reset() so the new main.py runs.
#
# Works in all states: console up, plain REPL, raw REPL leftover, or no
# main.py yet (first boot).
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

# 1. Probe the REPL state and ask the FM console (if running) to exit.
echo "==> probing the REPL state..."
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

def drain(sec):
    end = time.time() + sec
    out = b""
    while time.time() < end:
        b = s.read(512)
        if b:
            out += b
    return out

buf = drain(0.5)
if b"raw REPL" in buf:
    # Left over from a previous mpremote session: leave raw REPL (Ctrl-B).
    print("   leftover raw-REPL state, exiting it")
    s.write(b"\x02")
    buf += drain(0.3)

# Send CR to force a fresh prompt whatever the state, then look at it.
s.write(b"\r")
buf += drain(0.5)

sent = 0
state = None
for _ in range(5):
    if b">>>" in buf:
        state = "repl"
        break
    if b"fm>" in buf:
        if sent < 3:
            s.write(b"exit\r\n")
            sent += 1
            print("   FM console detected, sending 'exit' (try %d)" % sent)
        buf += drain(1.0)
        if b">>>" in buf:
            state = "repl"
            print("   console exited, back at the plain REPL")
            break
    else:
        buf += drain(1.0)

if state == "repl":
    print("   board is at the plain MicroPython REPL")
else:
    print("   WARNING: could not identify the REPL state; captured: %r" % buf)
    print("   (continuing - mpremote may still recover)")
s.close()
EOF

# 2. Upload without a soft reset (the board is at the plain REPL now).
echo "==> uploading $(basename "${SRC}") to :main.py"
mpremote resume fs cp "${SRC}" :main.py

# 3. Reset so the new main.py runs.  The USB port vanishes mid-reset, which
#    makes mpremote throw an OSError (expected - the board is rebooting).
echo "==> resetting the board (USB port will briefly vanish)..."
mpremote resume exec "import machine; machine.reset()" >/dev/null 2>&1 || true

echo "==> done - the new main.py is running. Open a serial terminal (115200)."
