# RP2040 RF Transmitter console for the RPI_PICO_FM MicroPython firmware.
#
# Starts the transmitter, then provides an interactive serial console.
# Connect with a serial terminal (screen/minicom/PuTTY on the CDC port,
# 115200 baud) or run from Thonny.  Type `help` for the command list.
#
# Carrier/deviation can be persisted: `reinit` saves them to /fm_cfg.json and
# reboots, so the new values are used on every boot.
#
# WARNING: GPIO21 outputs a strong RF signal. Do NOT attach an antenna wire;
# radiating from this pin is most likely illegal in your country.

import array
import json
import machine
import os
import select
import sys
import time

import pico_fm

VERSION = "0.22.9"          # console release version (see release README)

# SHA-256 of the firmware this console is shipped with
# (release/firmware/rp2040pico_fm_firmware.uf2).  Shown by `ver`.
FW_SHA256 = "3f67804c61060afd78fbebb415e41a18d3eebb621c11d41706a6a6f6fb992bfd"

# Project links shown by the `ver` command.
PROJECT_URL = "https://git.sr.ht/~bytewolf/rp2040-fm-transmitter"
BILIBILI_URL = "https://space.bilibili.com/28447213"

RF_PIN = 21                # GP21 = CLK_GPOUT0 (configurable: 21/23/24/25)

# LED configuration: False = plain PWM LED on GPIO25 (or ledpin <0-29>);
# True = WS2812 NeoPixel (e.g. RP2040-Zero onboard LED), driven from Python.
LED_WS2812 = False
WS2812_PIN = 16

CFG_FILE = "/fm_cfg.json"
DEFAULT_CARRIER = 87_900_000
DEFAULT_DEVIATION = 75_000

# Default audio-processing settings (restored by `reset`).
# PREEMPH: "on" = 75us (default), "50" = 50us, "off" = disabled.
DEFAULT_PREEMPH = "on"
# Weak-sample mute (sq) threshold in % of full scale; 0 = off.
DEFAULT_SQUELCH = 0

# Broad limits for the `reinit` command (the PLL itself further restricts
# what is actually achievable; init() raises if a range is unusable).
REINIT_CARRIER_MIN = 10_000_000
REINIT_CARRIER_MAX = 160_000_000
REINIT_DEV_MIN = 1000
REINIT_DEV_MAX = 500_000

# Console-tracked state (the firmware exposes the audio params but not these
# switches, so the console remembers what it set).
RF_ON = True
AUDIO_ON = True
# "on" | "50" | "off" - current pre-emphasis setting (on == 75us).
PREEMPH = DEFAULT_PREEMPH
# Squelch threshold in % of full scale (0 = off), mirrored from the firmware.
SQUELCH_PCT = DEFAULT_SQUELCH


def pll_limits():
    """(lo, hi) actual PLL output range in Hz, and max sensible deviation."""
    lo, hi = pico_fm.range()
    return lo, hi, (hi - lo) // 2


def build_help():
    """Compact command reference (static text).  Full dynamic ranges are
    shown by `status`; key behaviour notes are kept inline."""
    return """\
RP2040 RF Transmitter console - commands (values in Hz unless stated):
  help                 show this help
  ver                  version, firmware sha256, project links
  status               show all audio/FM parameters (incl. PLL range)
  freq <Hz>            fine-tune carrier (outside the PLL range -> reboot)
  dev <Hz>             set full-scale deviation (max: half the PLL range)
  reinit <car> <dev> [pin]   save band/deviation/RF pin, then reboot
  pdm <1|2|3|4>       PDM dither rate in MHz (1 = default; 2+ lowers
                      narrowband noise; saves and reboots)
  refdiv <1|2>        PLL reference divider (2 = half PDM step, ~6dB less
                      narrowband noise; saves and reboots)
  pin <21|23|24|25>    change RF output GPIO (saves and reboots)
  pwr <2|4|8|12>       RF output drive strength in mA (12 = max, default)
  rf on|off            RF output on/off
  audio on|off         USB-audio -> FM routing on/off
  vol <0-100>          volume in percent (mute == vol 0)
  mute on|off          mute the audio stream
  pre on|off|50|75     15kHz band-limit + pre-emphasis (50/75us) + limiter
  sq <0-100>           mute weak samples below this % of full scale (0 = off)
  led <0|1|2|3>        LED mode: 0 off, 1 always, 2 stream, 3 audio VU
  ledpin <0-29>        set plain-LED GPIO (saves and reboots)
  ledpin ws2812 [gpio] use a WS2812 NeoPixel as the status LED
  vbar                 audio level meter (T = sq threshold; 'silent' when quiet)
  ring                 ring buffer fill % (watch for under/overflow)
  diag                 ISR/RX rates over 1s (both ~48000/s while streaming)
  pwm                  PWM slice registers + ISR cost
  cls                  clear the terminal screen
  sweep [lo hi step]   pause audio and sweep the carrier (PLL self-test)
  pll                  PLL diagnostics (ready/range/last written freq)
  reboot               save current settings and reboot (keep config)
  reset                delete saved config and reboot to defaults
  exit                 stop the console (back to the REPL)
"""


# Detailed help for `help <command>`: purpose, arguments, defaults, example.
CMD_DETAILS = {
    "help": """\
help [command] - show this list, or detailed help for one command.
  help               list all commands (short form)
  help <command>     detailed help for that command, e.g. help reinit
  ?                  same as help""",
    "ver": """\
ver - show version, firmware sha256 and project links.
  Useful to check the console matches the flashed firmware (see FW_SHA256).
  Example: ver""",
    "status": """\
status (alias: s) - show all audio/FM parameters.
  Includes: PLL ready/range, RF output, carrier, deviation, audio routing,
  ring fill, volume, mute, pre-emphasis, squelch, clips, RF power, LED.
  Example: status""",
    "freq": """\
freq <Hz> - fine-tune the carrier frequency.
  Live within the current PLL range (shown by `status`); outside the range
  it saves the target and reboots to it (like reinit).
  Default carrier: %d Hz.  Range: %d..%d Hz.
  Example: freq 88000000""" % (DEFAULT_CARRIER, REINIT_CARRIER_MIN, REINIT_CARRIER_MAX),
    "dev": """\
dev <Hz> - set the full-scale deviation.
  A full-scale audio sample shifts the carrier by +/- this amount.
  Range: %d..%d Hz (upper bound = half the PLL range; `status` shows it).
  Default: %d Hz.
  Example: dev 40000""" % (REINIT_DEV_MIN, REINIT_DEV_MAX, DEFAULT_DEVIATION),
    "reinit": """\
reinit <carrier_Hz> <deviation_Hz> [rf_pin] - switch band and reboot.
  Saves the new carrier/deviation (and optional RF pin) to %s, then reboots.
  carrier %d..%d Hz (FM 88..108M, 2m 144..148M),
  deviation %d..%d Hz, pin 21/23/24/25.
  Example: reinit 98000000 50000 21""" % (CFG_FILE, REINIT_CARRIER_MIN, REINIT_CARRIER_MAX,
                                          REINIT_DEV_MIN, REINIT_DEV_MAX),
    "pdm": """\
pdm <1|2|3|4> - PDM dither rate in MHz (experimental narrowband-noise control).
  The fractional PLL dithers the feedback divider at this rate; the PLL loop
  filter averages it, but imperfectly - the residual ripple is what narrowband
  (12.5/25kHz channel) radios hear as noise.  A faster rate is averaged more
  strongly (less noise); 2 MHz usually helps, >2 MHz is close to the M0+
  systick latency limit (test on hardware).  1 MHz = default/library-tested.
  Saves to %s and reboots (takes effect on the next boot).
  Example: pdm 2""" % CFG_FILE,
    "refdiv": """\
refdiv <1|2> - PLL reference divider (experimental narrowband-noise control).
  REFDIV=2 halves the feedback-divider step (12MHz -> 6MHz per fbdiv LSB), so
  the PDM dither amplitude and the residual ripple drop by ~6dB.  Legal per the
  RP2040 datasheet (PFD >= 5MHz) but lock/jitter should be re-verified.
  Saves to %s and reboots (takes effect on the next boot).
  Example: refdiv 2""" % CFG_FILE,
    "pin": """\
pin <21|23|24|25> - change the RF output GPIO, save and reboot.
  RF can ONLY go to 21/23/24/25: the RP2040 clock-output mux (clk_gpout0-3)
  is hardwired to these pins.  Original Pico / Pico W: only GPIO21 is
  broken out (23/24/25 internal; on Pico W taken by WiFi; 25 = LED).
  RP2040-Zero and many clones also expose 23/24.
  Example: pin 21""",
    "pwr": """\
pwr <2|4|8|12> - RF output drive strength in mA (transmit power).
  Default: 12 mA (max).  8/12 mA give a more symmetric square wave (better
  even-order harmonic suppression); odd harmonics are inherent to the
  square wave and unchanged.  Use a real 50-ohm load to see the difference.
  Example: pwr 8""",
    "rf": """\
rf on|off - enable/disable the RF output.
  Turning the output off silences the radio completely (no carrier).
  Example: rf on""",
    "audio": """\
audio on|off - start/stop the USB-audio -> FM routing.
  `audio off` silences the radio (carrier parks on fc) and is reported as
  muted to the host (Windows shows the mute state).  `audio on` resumes.
  Example: audio on""",
    "vol": """\
vol <0-100> - set the volume in percent (mapped to the internal gain).
  0 = muted; 100 = full scale.  Keep the PC volume moderate so the limiter
  is not constantly engaged (watch clips in `vbar`/`status`).
  Example: vol 70""",
    "mute": """\
mute on|off - mute/unmute the audio stream (routing stays on).
  `mute on` silences the radio immediately and is reported as muted to the
  host (Windows shows the mute state).  `audio off` stops the routing
  instead; `vol 0` is another way to get a quiet carrier.
  Example: mute on""",
    "pre": """\
pre on|off|50|75 - 15kHz band-limit + pre-emphasis + soft limiter.
  on = 75us (default, US/EU broadcast), 50 = 50us (China/Japan), off = no
  processing.  Pick the value matching your radio's de-emphasis to reduce
  high-frequency hiss.  Saved to %s.
  Example: pre 50""" % CFG_FILE,
    "sq": """\
sq <0-100> - weak-sample mute threshold in % of full scale.
  Samples below the threshold are zeroed (not modulated), so faint
  background noise in the stream does not reach the radio.  0 = off
  (default).  Paused/muted audio is silent anyway (carrier parks on fc).
  Watch the T marker in `vbar` while tuning.
  Example: sq 5""",
    "led": """\
led <0|1|2|3> - LED mode.
  0 = off, 1 = always on, 2 = on while streaming, 3 = audio-level VU
  (default).  With `ledpin ws2812`, mode 3 is a green->yellow->red VU.
  Example: led 2""",
    "ledpin": """\
ledpin <0-29> - set the plain-LED GPIO, save and reboot.
  Default: 25 (Pico onboard LED).
  Example: ledpin 25""",
    "vbar": """\
vbar - single-line audio level meter (~50 fps).
  Shows the peak bar, the sq threshold (T marker) and the clip rate;
  displays "silent" with a slow refresh while the input is quiet.
  Any key (or Ctrl-C) stops it.  Example: vbar""",
    "ring": """\
ring - ring buffer fill in percent.
  Near 0% = producer/consumer balanced; 99% = the consumer stalled
  (check `diag`).  Example: ring""",
    "diag": """\
diag - measure ISR/RX rates over 1 second.
  Both should be ~48000/s while streaming.  A low ISR rate means the
  48kHz sample IRQ is being starved.
  Example: diag""",
    "pwm": """\
pwm - PWM slice-7 registers + measured ISR cost (diagnostics).
  Expected: TOP=999, DIV=/1.0, clk_sys=48000000, ISR cost ~2-6 us.
  Example: pwm""",
    "cls": """\
cls - clear the terminal screen (ANSI escape).  Example: cls""",
    "sweep": """\
sweep [lo hi step] - pause audio and sweep the carrier (PLL self-test).
  Defaults: the current PLL range, step 500 kHz.  Values are clamped to
  the PLL range.  Watch the signal move on a spectrum analyser / radio.
  Example: sweep 87000000 88500000 500000""",
    "pll": """\
pll - PLL diagnostics: ready state, achievable range, last written freq.
  Example: pll""",
    "reboot": """\
reboot - save the current settings to %s and reboot without resetting
  them (same effect as unplugging/replugging).  Use it when a change only
  takes effect after a replug.  Example: reboot""" % CFG_FILE,
    "reset": """\
reset - delete the saved config (%s) and reboot to defaults
  (87.9 MHz / 75 kHz / GPIO21 / pre 75us / sq off).
  Example: reset""" % CFG_FILE,
    "exit": """\
exit - stop the console and return to the MicroPython REPL.
  Example: exit""",
}


def parse_int(s, what):
    """Parse an integer argument; prints a friendly error on bad input."""
    try:
        return int(s)
    except (ValueError, TypeError):
        print("error: '%s' is not a number (%s)" % (s, what))
        return None


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def vol_pct(vol):
    """Internal gain (0..32767) -> percent, rounded (avoids off-by-one)."""
    return (vol * 100 + 16384) // 32767


def pct_to_vol(pct):
    """Percent (0..100) -> internal gain, rounded (avoids off-by-one)."""
    return (pct * 32767 + 50) // 100


def load_cfg():
    try:
        with open(CFG_FILE) as f:
            return json.load(f)
    except OSError:
        return {}


def cfg_int(cfg, key, default):
    """Read an integer config value defensively.  A JSON file edited by hand
    (or written by an older version) may hold a numeric string or a float;
    anything non-numeric falls back to `default` so setup never crashes."""
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def save_cfg(cfg):
    try:
        with open(CFG_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError:
        print("(warning: cannot write %s)" % CFG_FILE)


def save_current(carrier=None, deviation=None, rf_pin=None, pdm_rate=None, refdiv=None):
    """Persist the current transmitter settings (defaults = current values)."""
    save_cfg({
        "carrier": carrier if carrier is not None else pico_fm.carrier(),
        "deviation": deviation if deviation is not None else pico_fm.deviation(),
        "rf_pin": rf_pin if rf_pin is not None else RF_PIN,
        "power_ma": pico_fm.power(),
        "led_pin": "ws2812" if LED_WS2812 else pico_fm.led_pin(),
        "ws2812_pin": WS2812_PIN if LED_WS2812 else 16,
        "preemph": PREEMPH,
        "squelch": SQUELCH_PCT,
        "pdm_rate": pdm_rate if pdm_rate is not None else pico_fm.pdm_rate(),
        "refdiv": refdiv if refdiv is not None else pico_fm.refdiv(),
    })


def start_ws2812_vu(pin):
    """Drive a WS2812 (NeoPixel, e.g. the RP2040-Zero onboard LED on GPIO16)
    as the status LED: follows pico_fm.led_mode() (0=off, 1=white always,
    2=white while streaming, 3=audio-level green->yellow->red VU).  Runs on a
    30 Hz soft timer so it works alongside the console."""
    from machine import Timer
    import neopixel
    np = neopixel.NeoPixel(machine.Pin(pin), 1)
    peak = [0]
    last_mode = [None]

    def tick(t):
        try:
            mode = pico_fm.led_mode()
            if mode != last_mode[0]:
                last_mode[0] = mode
                if mode == 0:
                    peak[0] = 0
            if mode == 0:
                np[0] = (0, 0, 0)
            elif mode == 1:
                np[0] = (255, 255, 255)
            elif mode == 2:
                np[0] = (255, 255, 255) if pico_fm.audio_active() else (0, 0, 0)
            else:  # mode 3: VU meter
                raw = pico_fm.samples(256)
                pk = 0
                for v in array.array("h", raw):
                    a = v if v >= 0 else -v
                    if a > pk:
                        pk = a
                if pk > peak[0]:
                    peak[0] = pk
                else:
                    peak[0] = peak[0] * 15 // 16  # slow decay
                lvl = peak[0] * 255 // 32768
                if lvl > 255:
                    lvl = 255
                # green -> yellow -> red
                if lvl < 128:
                    np[0] = (lvl * 2, 255, 0)
                else:
                    np[0] = (255, 255 - (lvl - 128) * 2, 0)
            np.write()
        except Exception:
            pass  # never let the LED timer kill anything

    return Timer(period=33, mode=Timer.PERIODIC, callback=tick)


def show_status():
    lo, hi = pico_fm.range()
    cf = pico_fm.current_freq()
    print("----- RP2040 RF Transmitter status -----")
    print("PLL ready      : %s" % ("yes" if pico_fm.ready() else "NO"))
    print("RF output      : %s (GPIO%d)" % ("ON" if RF_ON else "OFF", RF_PIN))
    print("Carrier        : %.3f MHz (PLL range %.3f..%.3f MHz)"
          % (pico_fm.carrier() / 1e6, lo / 1e6, hi / 1e6))
    print("Last ISR freq  : %s" % ("%.3f MHz" % (cf / 1e6) if cf else "(ISR never ran)"))
    print("Deviation      : %.1f kHz" % (pico_fm.deviation() / 1e3))
    print("Audio routing  : %s" % ("ON" if AUDIO_ON else "OFF"))
    print("Host streaming : %s" % ("yes" if pico_fm.audio_active() else "no"))
    print("Ring buffer    : %d%% full" % pico_fm.ring_level())
    print("Volume         : %d%%" % vol_pct(pico_fm.volume()))
    print("Mute           : %s" % ("yes" if pico_fm.muted() else "no"))
    pre_txt = {"on": "on (75us)", "50": "on (50us)", "off": "off"}.get(PREEMPH, "on (75us)")
    print("Pre-emphasis   : %s" % pre_txt)
    sq_txt = "off" if SQUELCH_PCT == 0 else "%d%%" % SQUELCH_PCT
    print("Squelch        : %s" % sq_txt)
    print("Clips          : %d (since boot)" % pico_fm.clips())
    print("RF power       : %d mA" % pico_fm.power())
    print("PDM dither     : %d MHz (PLL refdiv %d)" % (pico_fm.pdm_rate(), pico_fm.refdiv()))
    print("LED           : %s, mode %d (0=off 1=always 2=stream 3=VU)"
          % ("WS2812 GPIO%d" % WS2812_PIN if LED_WS2812
             else "GPIO%d" % pico_fm.led_pin(), pico_fm.led_mode()))
    print("---------------------------------")


def make_poller():
    """A select.poll() on stdin, or None if not supported."""
    try:
        p = select.poll()
        p.register(sys.stdin, select.POLLIN)
        # Drain any pending input so the first poll does not fire immediately.
        while p.poll(0):
            try:
                sys.stdin.read(1)
            except OSError:
                pass
        return p
    except Exception:
        return None


def peak_of(raw):
    vals = array.array("h", raw)
    pk = 0
    for v in vals:
        a = v if v >= 0 else -v
        if a > pk:
            pk = a
    return pk


def write_stdout(s):
    """Write to stdout without depending on sys.stdout.flush() (MicroPython's
    TextIOWrapper has none; CDC writes are transmitted immediately anyway)."""
    sys.stdout.write(s)
    try:
        sys.stdout.flush()
    except AttributeError:
        pass


def vbar():
    """Single-line audio level meter, refreshed at ~50 fps with \r redraw.
    Shows the audio peak, the squelch threshold (T marker, when enabled) and
    the clip rate.  While the input is silent the refresh drops to ~2 fps and
    shows "silent" - this avoids constant USB CDC traffic, whose digital
    activity couples noise into the RF path.  Any key (or Ctrl-C) stops it."""
    poller = make_poller()
    peak = 0
    print("vbar - audio level meter (T = squelch threshold), any key or Ctrl-C to stop")
    next_frame = time.ticks_ms()
    last_clips = pico_fm.clips()
    last_sec = next_frame
    clips_rate = 0
    silent = False
    while True:
        if poller is not None and poller.poll(0):
            try:
                sys.stdin.readline()
            except OSError:
                pass
            break
        now = time.ticks_ms()
        if now < next_frame:
            time.sleep_ms(2)
            continue
        # Clip rate, once per second (delta of the firmware clip counter).
        if now - last_sec >= 1000:
            clips_rate = pico_fm.clips() - last_clips
            last_clips += clips_rate
            last_sec = now
        raw = pico_fm.samples(256)
        pk = peak_of(raw)
        if pk > peak:
            peak = pk
        else:
            peak = (peak * 7) // 8  # short trail so the bar follows the beat
        if pk == 0:
            # Silent: throttle to ~2 fps so the USB CDC stream (and the
            # digital noise it couples into the RF) stays minimal.
            if not silent:
                silent = True
                write_stdout("\rsilent (any key or Ctrl-C to stop)   ")
            next_frame = now + 500
            continue
        silent = False
        next_frame = now + 20  # ~50 fps
        nbars = peak * 40 // 32768
        thr = pico_fm.squelch()
        thrpos = thr * 40 // 32768 if thr else -1
        row = []
        for i in range(40):
            if i == thrpos:
                row.append("T")
            elif i < nbars:
                row.append("#")
            else:
                row.append("-")
        write_stdout("\r[%s] %5d clips=%d" % ("".join(row), peak, clips_rate))
    print()


def do_command(line):
    global RF_ON, AUDIO_ON, PREEMPH, SQUELCH_PCT
    parts = line.split()
    if not parts:
        return True
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None
    arg2 = parts[2] if len(parts) > 2 else None
    lo, hi, dev_max = pll_limits()

    try:
        if cmd in ("help", "?"):
            if arg is None:
                print(build_help())
            else:
                d = CMD_DETAILS.get(arg.lower())
                if d is None:
                    print("no detailed help for '%s'" % arg)
                else:
                    print(d)
        elif cmd == "ver":
            print("RP2040 RF Transmitter v%s" % VERSION)
            print("Firmware sha256: %s" % FW_SHA256)
            try:
                mpy = sys.implementation.version
                print("MicroPython: %d.%d.%d" % mpy[:3])
            except Exception:
                pass
            print("Project: %s" % PROJECT_URL)
            print("Bilibili: %s" % BILIBILI_URL)
        elif cmd in ("status", "s"):
            show_status()
        elif cmd == "freq":
            if arg is None:
                print("usage: freq <Hz>  (current PLL range %d..%d Hz; "
                      "outside it -> reboots to that carrier)" % (lo, hi))
            else:
                v = parse_int(arg, "freq")
                if v is not None:
                    if lo <= v <= hi:
                        pico_fm.set_carrier(v)
                        print("carrier set to %.3f MHz (live, within current range)"
                              % (pico_fm.carrier() / 1e6))
                    else:
                        if v < REINIT_CARRIER_MIN or v > REINIT_CARRIER_MAX:
                            print("error: carrier out of supported range %d..%d Hz"
                                  % (REINIT_CARRIER_MIN, REINIT_CARRIER_MAX))
                        else:
                            print("target outside the current PLL range "
                                  "(%.3f..%.3f MHz) - saving and rebooting to %.3f MHz"
                                  % (lo / 1e6, hi / 1e6, v / 1e6))
                            save_current(carrier=v)
                            time.sleep_ms(100)
                            machine.reset()
        elif cmd in ("dev", "deviation"):
            if arg is None:
                print("usage: dev <Hz>  (range 1000..%d Hz)" % dev_max)
            else:
                v = parse_int(arg, "dev")
                if v is not None:
                    if v < 1000 or v > dev_max:
                        v = clamp(v, 1000, dev_max)
                        print("(clamped to %d Hz - half the PLL range)" % v)
                    pico_fm.set_deviation(v)
                    print("deviation set to %.1f kHz" % (pico_fm.deviation() / 1e3))
        elif cmd == "reinit":
            if arg is None or arg2 is None:
                print("usage: reinit <carrier_Hz> <deviation_Hz> [rf_pin]")
                print("  carrier %d..%d Hz, deviation %d..%d Hz, rf_pin 21/23/24/25"
                      % (REINIT_CARRIER_MIN, REINIT_CARRIER_MAX,
                         REINIT_DEV_MIN, REINIT_DEV_MAX))
            else:
                car = parse_int(arg, "carrier")
                dev = parse_int(arg2, "deviation")
                new_pin = RF_PIN
                if len(parts) > 3:
                    new_pin = parse_int(parts[3], "rf_pin")
                    if new_pin is None or new_pin not in (21, 23, 24, 25):
                        print("error: rf_pin must be 21, 23, 24 or 25")
                        new_pin = None
                if car is not None and dev is not None and new_pin is not None:
                    if not (REINIT_CARRIER_MIN <= car <= REINIT_CARRIER_MAX):
                        print("error: carrier out of range %d..%d Hz"
                              % (REINIT_CARRIER_MIN, REINIT_CARRIER_MAX))
                    elif not (REINIT_DEV_MIN <= dev <= REINIT_DEV_MAX):
                        print("error: deviation out of range %d..%d Hz"
                              % (REINIT_DEV_MIN, REINIT_DEV_MAX))
                    else:
                        save_current(carrier=car, deviation=dev, rf_pin=new_pin)
                        print("saved %.3f MHz / %.1f kHz / GPIO%d - rebooting..."
                              % (car / 1e6, dev / 1e3, new_pin))
                        time.sleep_ms(100)
                        machine.reset()
        elif cmd == "pdm":
            if arg is None or arg not in ("1", "2", "3", "4"):
                print("usage: pdm <1|2|3|4>  (PDM dither rate in MHz; 1 = default)")
                print("  A faster dither is averaged more strongly by the PLL loop")
                print("  filter, lowering the residual ripple heard by narrowband")
                print("  (12.5/25kHz) FM radios.  2 MHz usually helps; >2 MHz is")
                print("  experimental.  Saves and reboots (next boot applies it).")
            else:
                pico_fm.set_pdm_rate(int(arg))
                save_current()
                print("PDM rate set to %s MHz - rebooting..." % arg)
                time.sleep_ms(100)
                machine.reset()
        elif cmd == "refdiv":
            if arg is None or arg not in ("1", "2"):
                print("usage: refdiv <1|2>  (PLL reference divider; 1 = default)")
                print("  REFDIV=2 halves the PDM dither step (12MHz -> 6MHz per")
                print("  fbdiv), cutting the narrowband ripple by ~6 dB.  Saves")
                print("  and reboots (next boot applies it).")
            else:
                pico_fm.set_refdiv(int(arg))
                save_current()
                print("REFDIV set to %s - rebooting..." % arg)
                time.sleep_ms(100)
                machine.reset()
        elif cmd == "pin":
            if arg is None or arg not in ("21", "23", "24", "25"):
                print("usage: pin <21|23|24|25>  (RF output GPIO; saves and reboots)")
            else:
                new_pin = int(arg)
                if new_pin != RF_PIN:
                    save_current(rf_pin=new_pin)
                    print("RF pin changed to GPIO%d - rebooting..." % new_pin)
                    time.sleep_ms(100)
                    machine.reset()
                else:
                    print("RF pin is already GPIO%d" % RF_PIN)
        elif cmd == "reset":
            print("resetting to defaults - deleting %s and rebooting..." % CFG_FILE)
            try:
                os.remove(CFG_FILE)
            except OSError:
                pass
            time.sleep_ms(100)
            machine.reset()
        elif cmd == "reboot":
            # Keep the current configuration: persist it first, then reboot
            # (equivalent to unplugging/replugging, without losing settings).
            save_current()
            print("rebooting - settings saved to %s..." % CFG_FILE)
            time.sleep_ms(100)
            machine.reset()
        elif cmd == "rf":
            if arg not in ("on", "off"):
                print("usage: rf on|off")
            else:
                pico_fm.enable_output(arg == "on")
                RF_ON = arg == "on"
                print("RF output %s" % arg)
        elif cmd == "audio":
            if arg not in ("on", "off"):
                print("usage: audio on|off")
            else:
                pico_fm.audio(arg == "on")
                AUDIO_ON = arg == "on"
                print("audio routing %s" % arg)
        elif cmd == "vol":
            if arg is None:
                print("usage: vol <0-100>, e.g. vol 70")
            else:
                v = parse_int(arg, "volume (percent)")
                if v is not None:
                    if v < 0 or v > 100:
                        v = clamp(v, 0, 100)
                        print("(clamped to %d%%)" % v)
                    pico_fm.set_volume(pct_to_vol(v))
                    print("volume set to %d%%" % vol_pct(pico_fm.volume()))
        elif cmd == "mute":
            if arg not in ("on", "off"):
                print("usage: mute on|off")
            else:
                pico_fm.set_mute(arg == "on")
                print("mute %s" % arg)
        elif cmd == "pre":
            if arg in ("on", "off", "50", "75"):
                if arg in ("on", "75"):
                    PREEMPH = "on"
                    pico_fm.set_preemphasis(True)
                    pico_fm.set_preemphasis_tc(75)
                    print("pre-emphasis on (75us)")
                elif arg == "50":
                    PREEMPH = "50"
                    pico_fm.set_preemphasis(True)
                    pico_fm.set_preemphasis_tc(50)
                    print("pre-emphasis on (50us)")
                else:
                    PREEMPH = "off"
                    pico_fm.set_preemphasis(False)
                    print("pre-emphasis off")
                save_current()
            else:
                print("usage: pre on|off|50|75  (on = 75us)")
        elif cmd == "sq":
            if arg is None:
                print("usage: sq <0-100>  (0 = off, default)")
            else:
                v = parse_int(arg, "squelch (%)")
                if v is not None:
                    if v < 0 or v > 100:
                        print("error: squelch must be 0..100")
                    else:
                        SQUELCH_PCT = v
                        pico_fm.set_squelch(v * 32767 // 100)
                        print("squelch %s" % ("off" if v == 0 else "%d%%" % v))
                        save_current()
        elif cmd == "led":
            if arg is None or arg not in ("0", "1", "2", "3"):
                print("usage: led <0|1|2|3>  (0=off 1=always-on 2=stream 3=VU)")
            else:
                pico_fm.set_led_mode(int(arg))
                print("LED mode %s" % arg)
        elif cmd == "ledpin":
            if arg is None:
                print("usage:")
                print("  ledpin <0-29>            plain LED on a GPIO (saves, reboots)")
                print("  ledpin ws2812 [gpio]     WS2812 NeoPixel (default GPIO16,"
                      " e.g. RP2040-Zero onboard LED); led 0-3 control it")
            elif arg == "ws2812":
                wpin = 16
                if len(parts) > 2:
                    wpin = parse_int(parts[2], "ws2812 gpio")
                    if wpin is None or not (0 <= wpin <= 29):
                        print("error: ws2812 gpio must be 0..29")
                        wpin = None
                if wpin is not None:
                    save_cfg({"carrier": pico_fm.carrier(),
                              "deviation": pico_fm.deviation(),
                              "rf_pin": RF_PIN,
                              "power_ma": pico_fm.power(),
                              "led_pin": "ws2812",
                              "ws2812_pin": wpin,
                              "preemph": PREEMPH,
                              "squelch": SQUELCH_PCT})
                    print("LED set to WS2812 on GPIO%d - rebooting..." % wpin)
                    time.sleep_ms(100)
                    machine.reset()
            else:
                v = parse_int(arg, "ledpin")
                if v is not None:
                    if v < 0 or v > 29:
                        print("error: ledpin must be 0..29 or 'ws2812'")
                    else:
                        save_cfg({"carrier": pico_fm.carrier(),
                                  "deviation": pico_fm.deviation(),
                                  "rf_pin": RF_PIN,
                                  "power_ma": pico_fm.power(),
                                  "led_pin": v,
                                  "preemph": PREEMPH,
                                  "squelch": SQUELCH_PCT})
                        print("LED pin changed to GPIO%d - rebooting..." % v)
                        time.sleep_ms(100)
                        machine.reset()
        elif cmd in ("pwr", "power"):
            if arg is None or arg not in ("2", "4", "8", "12"):
                print("usage: pwr <2|4|8|12>  (RF output drive strength in mA; "
                      "higher = stronger signal, more harmonics)")
            else:
                pico_fm.set_power(int(arg))
                print("RF power set to %d mA" % pico_fm.power())
                save_current()
        elif cmd == "cls":
            # ANSI clear-screen + home; works on most serial terminals.
            write_stdout("\x1b[2J\x1b[H")
        elif cmd == "vbar":
            vbar()
        elif cmd == "ring":
            print("ring buffer %d%% full" % pico_fm.ring_level())
        elif cmd == "pwm":
            top, div, csr, clk = pico_fm.pwm_info()
            cost = pico_fm.isr_cost()
            i0 = pico_fm.diag()[0]
            time.sleep_ms(1000)
            i1 = pico_fm.diag()[0]
            print("clk_sys     : %d Hz" % clk)
            print("PWM slice7  : TOP=%d DIV=0x%04X CSR=0x%02X"
                  "  (INT=1 FRAC=0 -> /1.0, wrap 48kHz)" % (top, div, csr))
            print("ISR cost    : ~%d us/call (expect ~2-6)" % cost)
            print("ISR rate    : %d/s over 1s (expect ~48000)" % (i1 - i0))
        elif cmd == "diag":
            i0, r0 = pico_fm.diag()
            time.sleep_ms(1000)
            i1, r1 = pico_fm.diag()
            print("ISR ticks : %d -> %d   (+%d in 1s, expect ~48000)"
                  % (i0, i1, i1 - i0))
            print("RX frames : %d -> %d   (+%d in 1s, expect ~48000 while streaming)"
                  % (r0, r1, r1 - r0))
            print("ring %d%% full, last ISR freq %.3f MHz"
                  % (pico_fm.ring_level(), pico_fm.current_freq() / 1e6))
        elif cmd == "sweep":
            vlo = parse_int(arg if arg is not None else str(lo), "sweep lo")
            vhi = parse_int(arg2 if arg2 is not None else str(hi), "sweep hi")
            vstep = parse_int(parts[3] if len(parts) > 3 else "500000", "sweep step")
            if None not in (vlo, vhi, vstep):
                vlo = clamp(vlo, lo, hi)
                vhi = clamp(vhi, lo, hi)
                if vstep <= 0:
                    print("error: step must be positive")
                elif vlo >= vhi:
                    print("error: lo must be < hi (got %d >= %d)" % (vlo, vhi))
                else:
                    print("sweeping %.3f..%.3f MHz step %.0f kHz (audio paused)"
                          % (vlo / 1e6, vhi / 1e6, vstep / 1e3))
                    pico_fm.audio(False)
                    f = vlo
                    while f <= vhi:
                        pico_fm.set_freq(f)
                        time.sleep_ms(80)
                        f += vstep
                    pico_fm.audio(AUDIO_ON)
                    print("sweep done")
        elif cmd == "pll":
            cf = pico_fm.current_freq()
            print("ready: %s, range %.3f..%.3f MHz, last ISR freq: %s"
                  % ("yes" if pico_fm.ready() else "no", lo / 1e6, hi / 1e6,
                     "%.3f MHz" % (cf / 1e6) if cf else "(ISR never ran)"))
        elif cmd == "exit":
            print("bye")
            return False
        else:
            print("unknown command: %s  (type 'help')" % cmd)
    except Exception as e:
        # Never let a command error kill the console.
        print("error: %s" % e)
    return True


def wait_terminal():
    """Wait until a terminal/serial program actually has the CDC port open
    (DTR asserted), so the banner is printed to a live console."""
    if pico_fm.cdc_connected():
        return
    print("waiting for a terminal connection...", end="")
    while not pico_fm.cdc_connected():
        time.sleep_ms(250)
    print(" connected.")


def do_setup():
    """Transmitter setup: init, RF on, then wait for a terminal and print
    the banner.  Runs inside the retry loop so an interrupt during setup
    restarts it instead of dropping to the plain REPL."""
    global RF_PIN, LED_WS2812, WS2812_PIN, PREEMPH, SQUELCH_PCT
    cfg = load_cfg()
    carrier = cfg_int(cfg, "carrier", DEFAULT_CARRIER)
    deviation = cfg_int(cfg, "deviation", DEFAULT_DEVIATION)
    rf_pin = cfg_int(cfg, "rf_pin", RF_PIN)
    if rf_pin not in (21, 23, 24, 25):
        rf_pin = 21
    RF_PIN = rf_pin

    # Experimental narrowband-noise controls, applied BEFORE init() (both are
    # read once at PLL/core1 launch): REFDIV=2 halves the PDM step, and a
    # faster PDM dither is averaged more strongly by the PLL loop filter -
    # both lower the residual ripple heard by narrowband (12.5/25kHz) radios.
    refdiv = cfg_int(cfg, "refdiv", 1)
    if refdiv not in (1, 2):
        refdiv = 1
    pico_fm.set_refdiv(refdiv)
    pdm_rate = cfg_int(cfg, "pdm_rate", 1)
    if not (1 <= pdm_rate <= 4):
        pdm_rate = 1
    pico_fm.set_pdm_rate(pdm_rate)

    ok = False
    try:
        pico_fm.init(carrier, deviation, RF_PIN)
        ok = True
    except ValueError as e:
        print("ERROR: init with saved config failed: %s" % e)
        print("Falling back to defaults %.3f MHz / %.1f kHz"
              % (DEFAULT_CARRIER / 1e6, DEFAULT_DEVIATION / 1e3))
        try:
            os.remove(CFG_FILE)
        except OSError:
            pass
        try:
            pico_fm.init(DEFAULT_CARRIER, DEFAULT_DEVIATION, RF_PIN)
            ok = True
        except ValueError as e:
            print("ERROR: default config also failed: %s" % e)
    if ok:
        pico_fm.enable_output(True)
        pico_fm.audio(True)
        # Apply persisted power, then set up the LED (plain PWM GPIO or WS2812).
        power_ma = cfg_int(cfg, "power_ma", 12)
        if power_ma not in (2, 4, 8, 12):
            power_ma = 12
        pico_fm.set_power(power_ma)
        # Apply persisted audio-processing settings: pre-emphasis (50/75us/off)
        # and the squelch (noise gate) threshold.
        pre = cfg.get("preemph", DEFAULT_PREEMPH)
        if pre not in ("on", "50", "75", "off"):
            pre = DEFAULT_PREEMPH
        PREEMPH = pre
        if pre == "off":
            pico_fm.set_preemphasis(False)
        else:
            pico_fm.set_preemphasis(True)
            pico_fm.set_preemphasis_tc(50 if pre == "50" else 75)
        sq = cfg_int(cfg, "squelch", DEFAULT_SQUELCH)
        if not (0 <= sq <= 100):
            sq = DEFAULT_SQUELCH
        SQUELCH_PCT = sq
        pico_fm.set_squelch(SQUELCH_PCT * 32767 // 100)
        led_pin = cfg.get("led_pin", 25)
        if led_pin == "ws2812":
            LED_WS2812 = True
            wpin = cfg_int(cfg, "ws2812_pin", 16)
            if not (0 <= wpin <= 29):
                wpin = 16
            WS2812_PIN = wpin
            pico_fm.set_led_mode(0)  # plain-LED path off; Python drives the NeoPixel
            start_ws2812_vu(WS2812_PIN)
        else:
            LED_WS2812 = False
            led_pin = cfg_int(cfg, "led_pin", 25)
            if not (0 <= led_pin <= 29):
                led_pin = 25
            pico_fm.set_led_pin(led_pin)
            pico_fm.set_led_mode(3)  # LED follows the audio level
    # Only now wait for a terminal, then print the banner to it.
    wait_terminal()
    print("==============================================")
    print(" RP2040 RF Transmitter console v%s" % VERSION)
    print(" carrier %.3f MHz, deviation %.1f kHz, GPIO%d"
          % (carrier / 1e6, deviation / 1e3, RF_PIN))
    print("==============================================")
    if ok:
        print("Select 'RP2040 RF Transmitter' as audio output.")
        show_status()
    print("Type 'help' for the full command list.")


def console_loop():
    """The interactive prompt.  Returns only when the user types 'exit'."""
    while True:
        try:
            line = input("fm> ")
        except KeyboardInterrupt:
            # A Ctrl-C at the prompt (e.g. Thonny connect): just re-prompt.
            print("\n(interrupted - type 'exit' to return to the REPL)")
            time.sleep_ms(100)
            continue
        except EOFError:
            continue
        if not do_command(line):
            return


def main():
    # Setup retries on any interrupt/error, but capped so a persistently
    # broken config cannot spin forever (worst case: console runs without RF,
    # and the device always stays flashable via BOOTSEL).
    attempts = 0
    while True:
        try:
            do_setup()
            break
        except KeyboardInterrupt:
            print("\n(setup interrupted - retrying)")
            time.sleep_ms(300)
        except Exception as e:
            attempts += 1
            print("\n(setup error: %s - retrying)" % e)
            time.sleep_ms(300)
            if attempts >= 10:
                print("setup still failing after 10 tries - continuing to console")
                print("(if this repeats, /fm_cfg.json may be corrupt: type 'reset' to restore defaults)")
                break
    while True:
        try:
            console_loop()
            return
        except KeyboardInterrupt:
            print("\n(console resumed)")
            continue
        except Exception as e:
            print("\n(console error: %s - resumed)" % e)
            continue


main()
