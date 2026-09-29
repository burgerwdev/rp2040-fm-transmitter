#!/usr/bin/env sh
# pico_port.sh — print the RP2040 FM transmitter's USB CDC port.
#
# The board uses the project's own USB IDs (1209:fa50, see
# ports/rp2/boards/RPI_PICO_FM), so it is matched on VID:PID through sysfs
# instead of "the first /dev/ttyACM*".  That matters as soon as any other
# ttyACM/ttyUSB device is plugged in (a PlutoSDR, an ST-Link, another Pico):
# picking the wrong one gives "could not enter raw repl" or a stray login
# prompt.
#
# Resolution order:
#   1. $FM_PORT (explicit override)
#   2. sysfs VID:PID == 1209:fa50
#   3. /dev/pico            (udev symlink, tools/serial.sh suggests it)
#   4. /dev/serial/by-id/*RP2040*  (string fallback for renamed descriptors)
#
# On failure it prints the candidates to stderr and exits non-zero.
# Self-check (no hardware):  tools/pico_port.sh --self-test
set -eu

# Overridable for the self-test:
#   PICO_SYSFS        sysfs tty class directory
#   PICO_NO_FALLBACK  set to skip /dev/pico and /dev/serial/by-id
SYS_TTY="${PICO_SYSFS:-/sys/class/tty}"

find_port() {
    if [ -n "${FM_PORT:-}" ]; then
        echo "${FM_PORT}"
        return 0
    fi

    for d in "${SYS_TTY}"/ttyACM*; do
        [ -e "${d}" ] || continue
        vid="$(cat "${d}/device/../idVendor" 2>/dev/null || true)"
        pid="$(cat "${d}/device/../idProduct" 2>/dev/null || true)"
        if [ "${vid}:${pid}" = "1209:fa50" ]; then
            echo "/dev/$(basename "${d}")"
            return 0
        fi
    done

    if [ -z "${PICO_NO_FALLBACK:-}" ]; then
        [ -e /dev/pico ] && { echo /dev/pico; return 0; }
        for d in /dev/serial/by-id/*RP2040*; do
            [ -e "${d}" ] && { echo "${d}"; return 0; }
        done
    fi

    {
        echo "error: RP2040 RF transmitter (USB 1209:fa50) not found."
        echo "       candidates seen:"
        for d in "${SYS_TTY}"/ttyACM* "${SYS_TTY}"/ttyUSB*; do
            [ -e "${d}" ] || continue
            vid="$(cat "${d}/device/../idVendor" 2>/dev/null || true)"
            pid="$(cat "${d}/device/../idProduct" 2>/dev/null || true)"
            prod="$(cat "${d}/device/../product" 2>/dev/null || true)"
            echo "         /dev/$(basename "${d}")  ${vid}:${pid}  ${prod}"
        done
        echo "       pass one explicitly:  FM_PORT=/dev/ttyACM1 $0"
        echo "       (in BOOTSEL mode there is no CDC port at all)"
    } >&2
    return 1
}

self_test() {
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' EXIT
    # two candidate devices: a PlutoSDR on ttyACM0 and the Pico on ttyACM1
    mkdir -p "${tmp}/ttyACM0/device" "${tmp}/ttyACM1/device"
    printf '0456\n' > "${tmp}/ttyACM0/idVendor"
    printf 'b673\n' > "${tmp}/ttyACM0/idProduct"
    printf 'PlutoSDR (ADALM-PLUTO)\n' > "${tmp}/ttyACM0/product"
    printf '1209\n' > "${tmp}/ttyACM1/idVendor"
    printf 'fa50\n' > "${tmp}/ttyACM1/idProduct"
    printf 'RP2040 RF Transmitter\n' > "${tmp}/ttyACM1/product"

    got="$(env -u FM_PORT PICO_SYSFS="${tmp}" PICO_NO_FALLBACK=1 "$0")"
    [ "${got}" = "/dev/ttyACM1" ] || {
        echo "FAIL: expected /dev/ttyACM1, got '${got}'" >&2; exit 1; }

    rm -rf "${tmp}/ttyACM1"
    if env -u FM_PORT PICO_SYSFS="${tmp}" PICO_NO_FALLBACK=1 "$0" >/dev/null 2>&1; then
        echo "FAIL: expected a non-zero exit when the board is absent" >&2; exit 1
    fi

    got="$(PICO_SYSFS="${tmp}" PICO_NO_FALLBACK=1 FM_PORT=/dev/ttyACM9 "$0")"
    [ "${got}" = "/dev/ttyACM9" ] || {
        echo "FAIL: FM_PORT override ignored (got '${got}')" >&2; exit 1; }

    echo "pico_port.sh self-test: OK (picks 1209:fa50 among candidates,"
    echo "                        fails cleanly when absent, honours FM_PORT)"
}

case "${1:-}" in
    --self-test) self_test ;;
    *) find_port ;;
esac
