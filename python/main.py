# FM transmitter console for the RPI_PICO_FM MicroPython firmware.
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

VERSION = "0.22.1"          # console release version (see release README)

# SHA-256 of the firmware this console is shipped with
# (release/firmware/rp2040pico_fm_firmware.uf2).  Shown by `ver`.
FW_SHA256 = "c3f3e8034174fc778d242a5f7dc22372a2a60e56e78dcd345b569e83aa7c60c5"

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

# Broad limits for the `reinit` command (the PLL itself further restricts
# what is actually achievable; init() raises if a range is unusable).
REINIT_CARRIER_MIN = 10_000_000
REINIT_CARRIER_MAX = 160_000_000
REINIT_DEV_MIN = 1000
REINIT_DEV_MAX = 500_000

# Console-tracked state (the firmware exposes the audio params but not these
# two switches, so the console remembers what it set).
RF_ON = True
AUDIO_ON = True


def pll_limits():
    """(lo, hi) actual PLL output range in Hz, and max sensible deviation."""
    lo, hi = pico_fm.range()
    return lo, hi, (hi - lo) // 2


def build_help():
    lo, hi, dev_max = pll_limits()
    return """\
FM transmitter console - commands (values in Hz unless stated):
  help                 show this help
  ver                  show version, firmware sha256 and project links
  status               show all audio/FM parameters
  freq <Hz>            fine-tune the carrier within the current PLL range
                       (%d..%d Hz).  Outside it, reboots to that carrier
                       (like reinit).  e.g. freq %d
  dev <Hz>             set deviation. Range: 1000..%d Hz
                       e.g. dev 50000
  reinit <car> <dev> [pin]   switch carrier band / deviation / RF pin and reboot.
                       carrier %d..%d Hz (FM 88..108M, 2m 144..148M),
                       deviation %d..%d Hz, pin 21/23/24/25
                       e.g. reinit 98000000 50000 21
  pin <21|23|24|25>   change the RF output GPIO (saves and reboots).
                       NOTE: RF can ONLY go to 21/23/24/25 - the RP2040 clock
                       output mux (clk_gpout0-3) is hardwired to these pins.
                       Original Pico / Pico W: only GPIO21 is broken out
                       (23/24/25 internal; on Pico W taken by WiFi; 25 = LED).
                       RP2040-Zero and many clones also expose 23/24.
  reset               delete the saved config and reboot to defaults
  out on|off           enable/disable the RF output
  audio on|off         start/stop USB-audio -> FM routing
  vol <0-100>          set volume in percent (mapped to the internal gain);
                       e.g. vol 70   (mute == vol 0; audio off != mute)
  mute on|off          mute the audio stream
  pre on|off           15kHz band-limit + 75us pre-emphasis + limiter
  led <0|1|2|3>        LED mode: 0=off 1=always-on 2=on-while-streaming
                       3=audio-level VU
  ledpin <0-29>        set the plain-LED GPIO (saves and reboots)
  ledpin ws2812 [gpio] use a WS2812 NeoPixel as the status LED (default
                       GPIO16, e.g. the RP2040-Zero onboard LED); led 0-3
                       control it (3 = green->yellow->red audio VU)
  power <2|4|8|12>     RF output drive strength in mA (12 = max, default).
                       Measured: 8/12 mA give a more symmetric square wave
                       (better even-order harmonic suppression); odd harmonics
                       are inherent to the square wave and unchanged.
  vbar                 single-line audio level bar (~50 fps, \\r redraw);
                       any key or Ctrl-C stops it
  ring                 ring buffer fill %% (watch for under/overflow)
  diag                 measure ISR / RX rates over 1s (both ~48000/s)
  pwm                  PWM slice7 registers + measured ISR cost (diagnostics)
  sweep [lo hi step]   stop audio, sweep carrier lo..hi Hz step Hz, restore
                       values are clamped to %d..%d Hz
                       e.g. sweep 87000000 88500000 500000
  pll                  PLL diagnostics (ready + range + last written freq)
  exit                 stop the console (back to the REPL)
""" % (lo, hi, (lo + hi) // 2, dev_max,
       REINIT_CARRIER_MIN, REINIT_CARRIER_MAX, REINIT_DEV_MIN, REINIT_DEV_MAX,
       lo, hi)


def parse_int(s, what):
    """Parse an integer argument; prints a friendly error on bad input."""
    try:
        return int(s)
    except (ValueError, TypeError):
        print("error: '%s' is not a number (%s)" % (s, what))
        return None


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def load_cfg():
    try:
        with open(CFG_FILE) as f:
            return json.load(f)
    except OSError:
        return {}


def save_cfg(cfg):
    try:
        with open(CFG_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError:
        print("(warning: cannot write %s)" % CFG_FILE)


def save_current(carrier=None, deviation=None, rf_pin=None):
    """Persist the current transmitter settings (defaults = current values)."""
    save_cfg({
        "carrier": carrier if carrier is not None else pico_fm.carrier(),
        "deviation": deviation if deviation is not None else pico_fm.deviation(),
        "rf_pin": rf_pin if rf_pin is not None else RF_PIN,
        "power_ma": pico_fm.power(),
        "led_pin": "ws2812" if LED_WS2812 else pico_fm.led_pin(),
        "ws2812_pin": WS2812_PIN if LED_WS2812 else 16,
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
    print("----- FM transmitter status -----")
    print("PLL ready      : %s" % ("yes" if pico_fm.ready() else "NO"))
    print("RF output      : %s (GPIO%d)" % ("ON" if RF_ON else "OFF", RF_PIN))
    print("Carrier        : %.3f MHz (PLL range %.3f..%.3f MHz)"
          % (pico_fm.carrier() / 1e6, lo / 1e6, hi / 1e6))
    print("Last ISR freq  : %s" % ("%.3f MHz" % (cf / 1e6) if cf else "(ISR never ran)"))
    print("Deviation      : %.1f kHz" % (pico_fm.deviation() / 1e3))
    print("Audio routing  : %s" % ("ON" if AUDIO_ON else "OFF"))
    print("Host streaming : %s" % ("yes" if pico_fm.audio_active() else "no"))
    print("Ring buffer    : %d%% full" % pico_fm.ring_level())
    print("Volume         : %d%%" % (pico_fm.volume() * 100 // 32767))
    print("Mute           : %s" % ("yes" if pico_fm.muted() else "no"))
    print("Pre-emphasis   : %s" % ("on" if pico_fm.preemphasis() else "off"))
    print("RF power       : %d mA" % pico_fm.power())
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
    """Single-line audio level bar, refreshed at ~50 fps with \\r redraw
    (works on any serial terminal).  Any key (or Ctrl-C) stops it."""
    poller = make_poller()
    peak = 0
    print("vbar - audio level meter, any key or Ctrl-C to stop")
    next_frame = time.ticks_ms()
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
        next_frame = now + 20  # ~50 fps
        raw = pico_fm.samples(256)
        pk = peak_of(raw)
        if pk > peak:
            peak = pk
        else:
            peak = (peak * 7) // 8  # short trail so the bar follows the beat
        nbars = peak * 40 // 32768
        write_stdout("\r[%s] %5d" % ("#" * nbars + "-" * (40 - nbars), peak))
    print()


def do_command(line):
    global RF_ON, AUDIO_ON
    parts = line.split()
    if not parts:
        return True
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None
    arg2 = parts[2] if len(parts) > 2 else None
    lo, hi, dev_max = pll_limits()

    try:
        if cmd in ("help", "?"):
            print(build_help())
        elif cmd == "ver":
            print("Pico FM Transmitter v%s" % VERSION)
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
        elif cmd == "out":
            if arg not in ("on", "off"):
                print("usage: out on|off")
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
                    pico_fm.set_volume(v * 32767 // 100)
                    print("volume set to %d%%" % (pico_fm.volume() * 100 // 32767))
        elif cmd == "mute":
            if arg not in ("on", "off"):
                print("usage: mute on|off")
            else:
                pico_fm.set_mute(arg == "on")
                print("mute %s" % arg)
        elif cmd == "pre":
            if arg not in ("on", "off"):
                print("usage: pre on|off")
            else:
                pico_fm.set_preemphasis(arg == "on")
                print("pre-emphasis %s" % arg)
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
                              "ws2812_pin": wpin})
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
                                  "led_pin": v})
                        print("LED pin changed to GPIO%d - rebooting..." % v)
                        time.sleep_ms(100)
                        machine.reset()
        elif cmd == "power":
            if arg is None or arg not in ("2", "4", "8", "12"):
                print("usage: power <2|4|8|12>  (RF output drive strength in mA; "
                      "higher = stronger signal, more harmonics)")
            else:
                pico_fm.set_power(int(arg))
                print("RF power set to %d mA" % pico_fm.power())
                save_current()
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
    global RF_PIN, LED_WS2812, WS2812_PIN
    cfg = load_cfg()
    carrier = cfg.get("carrier", DEFAULT_CARRIER)
    deviation = cfg.get("deviation", DEFAULT_DEVIATION)
    rf_pin = cfg.get("rf_pin", RF_PIN)
    if rf_pin not in (21, 23, 24, 25):
        rf_pin = 21
    RF_PIN = rf_pin

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
        power_ma = cfg.get("power_ma", 12)
        if power_ma not in (2, 4, 8, 12):
            power_ma = 12
        pico_fm.set_power(power_ma)
        led_pin = cfg.get("led_pin", 25)
        if led_pin == "ws2812":
            LED_WS2812 = True
            wpin = cfg.get("ws2812_pin", 16)
            if not (0 <= wpin <= 29):
                wpin = 16
            WS2812_PIN = wpin
            pico_fm.set_led_mode(0)  # plain-LED path off; Python drives the NeoPixel
            start_ws2812_vu(WS2812_PIN)
        else:
            LED_WS2812 = False
            if not (0 <= led_pin <= 29):
                led_pin = 25
            pico_fm.set_led_pin(led_pin)
            pico_fm.set_led_mode(3)  # LED follows the audio level
    # Only now wait for a terminal, then print the banner to it.
    wait_terminal()
    print("==============================================")
    print(" Pico FM Transmitter console v%s" % VERSION)
    print(" carrier %.3f MHz, deviation %.1f kHz, GPIO%d"
          % (carrier / 1e6, deviation / 1e3, RF_PIN))
    print("==============================================")
    if ok:
        print("pico_fm.init() OK - RF on. Select 'Pico FM Sound Card' as output.")
        show_status()
    print(build_help())


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
