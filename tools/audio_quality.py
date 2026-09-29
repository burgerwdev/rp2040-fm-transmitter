#!/usr/bin/env python3
"""Host-side fixed-point replica of the FM audio chain.

This ports the fixed-point arithmetic of `fm_modulator.c: fm_audio_process()`
(exact coefficient constants, int16 truncation and int32 wrapping) so the
transmitted-sound quality can be measured **on the host**, without flashing
or probing hardware.

Sub-commands:
    resp     frequency response of the chain (pre-limiter, small signal)
    thd      harmonic distortion for test tones, linear and hot levels
    limiter  peak deviation / clipping behaviour on a stress signal
    drift    asynchronous USB-vs-PWM clock drift through the ring buffer
    all      everything, as one report block

Usage:
    python3 tools/audio_quality.py all
    python3 tools/audio_quality.py resp --tc 75 --nfm
    python3 tools/audio_quality.py drift --ring 4096
"""

import argparse
import sys

import numpy as np

FS = 48_000

# --- constants copied verbatim from ports/rp2/fm_transmitter/fm_modulator.c --
FM_LP15_A_Q15 = 28168    # a = 1 - exp(-2*pi*15000/48000)  (15 kHz LP)
FM_LP3K_A_Q15 = 10644    # a = 1 - exp(-2*pi*3000/48000)   (3 kHz LP)
FM_HP300_A_Q15 = 31509   # a = exp(-2*pi*300/48000)        (300 Hz HP)
FM_LP75_A_Q15 = 7953     # a = 1 - exp(-1/(75us*48000))    (2.1 kHz LP)
FM_LP50_A_Q15 = 11169    # a = 1 - exp(-1/(50us*48000))    (3.2 kHz LP)
FM_LP300_A_Q15 = 2199    # a = 1 - exp(-1/(300us*48000))   (530 Hz LP)
FM_PREEMPH_G_Q10 = 5120  # shelf gain 5.0 (~ +14 dB)       (Q10)
FM_DCB_A_Q15 = 9         # 2 Hz DC blocker
FM_LIM_SOFT = 26214      # soft-knee entry, 80% of full scale

TC_TO_COEF = {50: FM_LP50_A_Q15, 75: FM_LP75_A_Q15, 300: FM_LP300_A_Q15}


def i16(v):
    """C `(int16_t)v`: truncating (wrapping) conversion, not saturation."""
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def i32(v):
    """C int32_t wrap-around."""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


class Chain:
    """Replica of fm_audio_process() state and arithmetic."""

    def __init__(self, preemph=True, tc_us=75, nfm=False, squelch=0, lponly=False):
        self.preemph = preemph
        self.lponly = lponly
        self.lp_a = FM_LP3K_A_Q15 if nfm else FM_LP15_A_Q15
        self.lp75_a = TC_TO_COEF.get(tc_us, FM_LP75_A_Q15)
        self.nfm = nfm
        self.squelch = squelch
        self.clips = 0
        self.lp15 = self.lp75 = self.lp15b = 0
        self.dcb_xprev = self.dcb_yprev = 0
        self.hp_xprev = self.hp_yprev = 0

    def process(self, x):
        lp_a = self.lp_a

        # 2 Hz DC blocker (note: s_dcb_yprev stores the *unclamped* value,
        # s_dcb_xprev the raw input - replicated exactly).
        dcb = i32(self.dcb_yprev + (x - self.dcb_xprev)
                  - ((self.dcb_yprev * FM_DCB_A_Q15) >> 15))
        self.dcb_xprev = x
        self.dcb_yprev = dcb
        if dcb > 32767:
            dcb = 32767
        elif dcb < -32768:
            dcb = -32768
        x = i16(dcb)

        # NFM 300 Hz high-pass
        if self.nfm:
            hp = (FM_HP300_A_Q15 * (x + self.hp_yprev - self.hp_xprev)) >> 15
            self.hp_xprev = x
            self.hp_yprev = hp
            if hp > 32767:
                hp = 32767
            elif hp < -32768:
                hp = -32768
            x = i16(hp)

        if self.squelch:
            if abs(x) < self.squelch:
                x = 0

        if not self.preemph:
            if self.nfm:
                lp = i32(self.lp15 + ((lp_a * (x - self.lp15)) >> 15))
                self.lp15 = lp
                x = i16(lp)
            return x

        lp15 = i32(self.lp15 + ((lp_a * (x - self.lp15)) >> 15))
        self.lp15 = lp15

        if self.lponly:
            # just the two band-limit poles, no pre-emphasis (isolates the LPF)
            y = i32(self.lp15b + ((lp_a * (lp15 - self.lp15b)) >> 15))
            self.lp15b = y
            return i16(y)

        lp75 = i32(self.lp75 + ((self.lp75_a * (lp15 - self.lp75)) >> 15))
        self.lp75 = lp75
        y = i32(lp15 + (((lp15 - lp75) * FM_PREEMPH_G_Q10) >> 10))

        if y > 32767:
            y = 32767
        elif y < -32768:
            y = -32768

        lp15b = i32(self.lp15b + ((lp_a * (y - self.lp15b)) >> 15))
        self.lp15b = lp15b
        y = lp15b

        if y > FM_LIM_SOFT:
            y = FM_LIM_SOFT + ((y - FM_LIM_SOFT) >> 1)
        elif y < -FM_LIM_SOFT:
            y = -FM_LIM_SOFT + ((y + FM_LIM_SOFT) >> 1)
        if y > 32767:
            y = 32767
            self.clips += 1
        elif y < -32768:
            y = -32768
            self.clips += 1

        return i16(y)

    def run(self, xs):
        return np.array([self.process(int(v)) for v in xs], dtype=np.int64)


def sine(f, n, amp):
    t = np.arange(n) / FS
    return np.rint(amp * np.sin(2 * np.pi * f * t)).astype(np.int64)


def tone_db(y, f):
    """Amplitude of the f-Hz component of y, in dB relative to full scale."""
    n = len(y)
    spec = np.fft.rfft(y.astype(np.float64) * np.hanning(n))
    k = int(round(f * n / FS))
    mag = 2.0 * abs(spec[k]) / (n * np.hanning(n).sum() / n)
    if mag <= 0:
        return -np.inf
    return 20 * np.log10(mag / 32768.0)


def thd(y, f, nharm=10):
    n = len(y)
    w = np.hanning(n)
    spec = np.abs(np.fft.rfft(y.astype(np.float64) * w))
    norm = w.sum() / 2.0
    def binamp(k):
        return max(spec[k - 1], spec[k], spec[k + 1]) / norm
    f0 = binamp(int(round(f * n / FS)))
    if f0 <= 0:
        return float("inf")
    h = []
    for m in range(2, nharm + 1):
        if f * m >= 0.95 * FS / 2:   # above Nyquist: cannot exist
            break
        h.append(binamp(int(round(f * m * n / FS))))
    return float(np.sqrt(np.sum(np.square(h))) / f0)


# ---------------------------------------------------------------------------
def cmd_resp(a):
    """Small-signal frequency response of the whole chain (pre-limiter path)."""
    freqs = [20, 50, 100, 300, 1000, 2000, 3000, 5000, 8000, 10000, 12000,
             14000, 15000, 16000, 18000, 20000, 22000, 23000]
    n = 4 * FS
    amp = 800  # small enough that the +14 dB shelf never reaches the limiter
    chains = [
        ("75us", Chain(preemph=True, tc_us=75)),
        ("50us", Chain(preemph=True, tc_us=50)),
        ("300us", Chain(preemph=True, tc_us=300)),
        ("off", Chain(preemph=False)),
        ("nfm300", Chain(preemph=True, tc_us=300, nfm=True)),
        ("lp2only", Chain(lponly=True)),
    ]
    print("Frequency response (dB re input; small signal, limiter not engaged)")
    rows = {}
    for name, ch in chains:
        rows[name] = {}
        for f in freqs:
            x = sine(f, n, amp)
            y = ch.run(x)
            rows[name][f] = tone_db(y[FS:], f) - tone_db(x[FS:], f)
    print("  %-8s" % "f (Hz)" + "".join("%-10s" % n for n, _ in chains))
    for f in freqs:
        print("  %-8d" % f + "".join("%-10.2f" % rows[n][f] for n, _ in chains))
    print()
    print("  Net received response = transmit response - standard receiver")
    print("  de-emphasis (the radio applies the exact inverse of the standard")
    print("  curve, so this is what the listener actually hears):")
    print("  %-8s %10s %10s %10s" % ("f (Hz)", "75us net", "50us net", "300us net"))
    for f in freqs:
        row = [rows[k][f] - 10 * np.log10(1 + (2 * np.pi * f * tc * 1e-6) ** 2)
               for k, tc in (("75us", 75), ("50us", 50), ("300us", 300))]
        print("  %-8d %10.2f %10.2f %10.2f" % (f, row[0], row[1], row[2]))
    print()
    print("  Notes:")
    print("   - the +14 dB shelf ceiling (20*log10(5)) vs the standard 75us")
    print("     curve (20*log10(15000/2122) = +17.0 dB at 15 kHz)")
    print("   - preemph off + wideband = NO band-limit at all (see the report)")
    return None


def cmd_thd(a):
    print("Harmonic distortion (THD, %), 1 s window, harmonics 2..10")
    print("  %-10s %-12s %-12s %-12s %s" % (
        "tone", "level", "preemph 75", "preemph off", "clips(75us)"))
    for f in (1000, 5000, 10000):
        for amp, label in ((4000, "linear"), (20000, "loud"), (32000, "over")):
            out = []
            clips = None
            for pre in (True, False):
                ch = Chain(preemph=pre, tc_us=75)
                y = ch.run(sine(f, FS, amp))
                out.append(thd(y[FS // 2:], f))
                if pre:
                    clips = ch.clips
            print("  %-10d %-12s %-12.3f %-12.3f %d" % (
                f, label, out[0] * 100, out[1] * 100, clips))
    print()


def cmd_limiter(a):
    """Peak deviation and clip rate on a stress signal (worst-case content)."""
    print("Limiter / peak-deviation behaviour")
    print("  a full-scale int16 sample maps to +/- deviation*32767/32768")
    print("  soft knee at 80%% (26214), 2:1 above, hard clip at full scale")
    print("  %-26s %10s %10s %10s" % ("signal", "peak_out", "peak/dev", "clips"))
    n = FS
    cases = [
        ("full-scale 1kHz sine", sine(1000, n, 32767)),
        ("full-scale 6kHz sine", sine(6000, n, 32767)),
        ("full-scale 12kHz sine", sine(12000, n, 32767)),
    ]
    # a pre-emphasised 12 kHz tone is the worst case for the shelf
    for name, x in cases:
        for pre in (True, False):
            ch = Chain(preemph=pre, tc_us=75)
            y = ch.run(x)
            tag = name + (" [+pre]" if pre else " [flat]")
            print("  %-26s %10d %10.4f %10d" % (
                tag, int(np.max(np.abs(y))),
                np.max(np.abs(y)) / 32768.0, ch.clips))
    # a step (transient) - shows the lack of look-ahead
    step = np.concatenate([np.zeros(FS // 2), np.full(FS // 2, 30000)])
    for pre in (True, False):
        ch = Chain(preemph=pre, tc_us=75)
        y = ch.run(step)
        i = FS // 2
        print("  %-26s overshoot=%d samples above knee, clips=%d" % (
            "step 0->30000 [%s]" % ("+pre" if pre else "flat"),
            int(np.sum(np.abs(y[i:i + 200]) > FM_LIM_SOFT)), ch.clips))
    print()


def cmd_drift(a):
    """Asynchronous producer (USB host clock) vs consumer (RP2040 PWM) drift."""
    ring = a.ring
    secs = a.seconds
    init = ring // 2
    print("Asynchronous sample-clock drift through the %d-sample ring" % ring)
    print("  producer = host USB 48 kHz clock, consumer = PWM 48 kHz from the")
    print("  RP2040 crystal.  No resampler, no feedback endpoint, no counter.")
    print("  ppm > 0: host clock faster (ring fills -> drops); < 0: slower.")
    print()
    print("  %-6s %-14s %-16s %-16s" % (
        "ppm", "time to hit", "glitch rate", "glitches in %.0fs" % secs))
    print("  %-6s %-14s %-16s %-16s" % (
        "", "the limit", "(samples/s)", ""))
    for ppm in (-100, -50, -25, -10, 10, 25, 50, 100):
        rate = FS * abs(ppm) * 1e-6          # samples/s of imbalance
        t_hit = init / rate
        total = max(0.0, rate * (secs - t_hit))
        print("  %-6d %-14s %-16.2f %-16.0f" % (
            ppm, "%.0f s" % t_hit, rate, total))
    print()
    print("  Ring depth = %d samples = %.1f ms of buffering; the ring starts" % (
        ring, 1000.0 * ring / FS))
    print("  centred, so it absorbs %.0f s of drift at 25 ppm before the first" % (
        init / (FS * 25e-6)))
    print("  glitch.  After that the glitch continues at the imbalance rate")
    print("  forever - two free-running crystals never re-lock.")
    print()
    print("  What a glitch is, in the current code:")
    print("   - ring empty -> s_cur_sample = 0: the carrier parks on fc for")
    print("     that tick (a dropout), instead of holding the last sample;")
    print("   - ring full  -> the host sample is dropped silently (a click).")
    print("  Neither event increments any counter, so `diag` cannot show it.")
    print()
    print("  Worked example, %.0f s at +50 ppm (host faster):" % secs)
    produced = int(FS * secs * (1 + 50e-6))
    consumed = int(FS * secs)
    print("    produced %d samples, consumed %d -> net +%d" % (
        produced, consumed, produced - consumed))
    print("    ring capacity %d -> %d dropped samples, first at ~%.0f s" % (
        ring - 1, max(0, produced - consumed - (ring - 1 - init)),
        (ring - 1 - init) / (FS * 50e-6)))
    print()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("resp", cmd_resp), ("thd", cmd_thd),
                     ("limiter", cmd_limiter), ("drift", cmd_drift)):
        p = sub.add_parser(name)
        p.set_defaults(func=fn)

    d = sub.choices["drift"]
    d.add_argument("--ring", type=int, default=4096)
    d.add_argument("--seconds", type=float, default=600.0)

    r = sub.choices["resp"]
    r.add_argument("--tc", type=int, default=75)
    r.add_argument("--nfm", action="store_true")

    p = sub.add_parser("all")
    p.set_defaults(func=None)

    a = ap.parse_args()
    if a.cmd == "all":
        for fn in (cmd_resp, cmd_thd, cmd_limiter, cmd_drift):
            ns = argparse.Namespace(ring=4096, seconds=600.0)
            print("=" * 72)
            print(fn.__name__.replace("cmd_", "").upper())
            print("=" * 72)
            fn(ns)
        return 0
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
