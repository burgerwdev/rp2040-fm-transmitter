# FM transmit audio-quality layer audit

> [中文](../zh/audio-quality.md) · English

This document audits, layer by layer, the code in this repository that affects
the **received** sound quality, and gives quantified limits plus an impact
ranking. Every number comes from the host-side script
`tools/audio_quality.py`, a line-by-line replica of the fixed-point semantics
and coefficient constants of `fm_modulator.c: fm_audio_process()`.

> **This analysis does not flash the board and does not measure hardware.**
> Every number below is reproducible on the host with:
> ```bash
> python3 tools/audio_quality.py resp
> python3 tools/audio_quality.py thd
> python3 tools/audio_quality.py limiter
> python3 tools/audio_quality.py drift
> ```

---

## 1. Layers and impact ranking

| Layer | Location | Limit | Audible effect | Priority |
|---|---|---|---|---|
| **L3 audio DSP chain** | `fm_modulator.c: fm_audio_process()` | non-standard pre-emphasis (+8.6 dB too much at 2 kHz, −6.3 dB too little at 15 kHz); two first-order band-limit poles (−2.2 dB at 10 kHz); no limiter look-ahead | **tonal error (midrange hump, dull top) + severe distortion on loud material (THD 18–26 %)** | **P0** |
| **L1 USB audio & asynchronous sample clock** | `shared/tinyusb/*`, `tud_audio_rx_done_isr()` | host 48 kHz and PWM 48 kHz come from two independent crystals; no resampler, no feedback endpoint, no drift counter | periodic clicks after long playback (starts after ~14 min and persists) | **P1** |
| **L5 PLL/PDM RF layer** | `pico_fractional_pll.c` | the instantaneous frequency dithers by `ref/div` at 1 MHz; the PLL loop filter averages it only partially | RF phase noise / receiver noise floor (worst on narrowband and weak signals) | **P1** |
| **L2 ring-buffer observability** | `fm_modulator.c: fm_pwm_wrap_handler()` | no drop/underflow counters; underflow zeroes the sample instead of holding it | silent itself, but **hides L1** and turns a drop into a dropout | **P2** |
| **L6 USB control surface & defaults** | `fm_modulator.c: fm_db256_to_gain()`, `main.py` | UAC1 volume maps **dB → linear gain** linearly, not dB → dB | volume slider is mis-calibrated (request −30 dB, get −6 dB) | **P2** |
| **L4 modulation mapping** | `fm_pwm_wrap_handler()` | `freq = carrier + (sample*dev)>>15` | none (0.99997× full deviation; negligible) | — |

Rationale: L3 decides the tone and distortion and is 100 % verifiable on the
host; L1/L5 decide long-listen and weak-signal usability; L2/L6 are correctness
and observability.

---

## 2. Layer-by-layer audit

### L3 audio DSP chain (P0)

With pre-emphasis on, the chain is: `2 Hz DC block → first-order 15 kHz LP →
pre-emphasis → first-order 15 kHz LP → 2:1 soft-knee limiter`.

**Defect 1: the pre-emphasis curve is not the standard one.** The code computes
`y = lp15 + (lp15 - lp75)*5`, i.e. a **shelf with a +14 dB ceiling**
(20·log10(5)) that starts bending around 1 kHz. Standard 75 µs pre-emphasis is
a +6 dB/octave rise, referenced to 0 dB at 2.12 kHz, reaching +17.0 dB at
15 kHz.

```
  f (Hz)  75us     (measured transmit response, dB)
  1000    7.75
  2000    11.33
  5000    13.20
  15000   10.80

  Net received response = transmit response - standard receiver de-emphasis
  (what the listener actually hears)
  f (Hz)     75us net
  1000           6.88
  2000           8.57
  5000           5.04
  10000         -1.44
  15000         -6.27
```

**Result: the listener hears a +8.6 dB midrange hump around 2 kHz and a −6.3 dB
collapse at 15 kHz.** This is not "pre-emphasis is off"; the *shape* is wrong.
Turning pre-emphasis off only makes the top end duller, because the receiver's
de-emphasis is a fixed −6 dB/octave.

**Defect 2: the pre-emphasis burns 7–8 dB of headroom.** Because 1 kHz is
already boosted by 7.75 dB:

```
  tone    level      preemph 75   preemph off  clips(75us)
  1000    loud          17.859 %      0.002 %        0
  5000    loud          25.945 %      0.000 %        0
  1000    linear         0.033 %      0.013 %        0
```

`loud` is a sine of amplitude 20000 (≈ −4 dBFS). The standard 75 µs curve has
only +0.87 dB at 1 kHz, so the same input would not reach the limiter. **The
distortion is therefore caused by the wrong pre-emphasis curve, not by the
limiter itself.**

**Defect 3: the band-limit is only two first-order poles.**

```
  f (Hz)  lp2only   (band-limit only, dB)
  5000     -0.66
  10000    -2.16
  15000    -3.67
  23000    -4.89
```

The passband is already 2.2 dB down at 10 kHz and 3.7 dB down at 15 kHz (large
for a 15 kHz broadcast band); the stopband at 23 kHz is only −4.9 dB, i.e. the
48 kHz sampling images are barely suppressed.

**Defect 4: disabling pre-emphasis also removes the band-limit.** The
`if (!s_preemphasis_enabled)` branch `return x` without any low-pass in
wideband mode; only narrowband keeps its 3 kHz low-pass. See the flat `off`
column above.

**Defect 5: no limiter look-ahead.** On a step input, 200 samples sit above the
soft knee:

```
  step 0->30000 [+pre]   overshoot=200 samples above knee, clips=0
```

(The soft knee is intentional programme processing and need not change, but it
amplifies defect 2.)

### L1 USB audio & asynchronous sample clock (P1)

- descriptor: UAC1, 48 kHz, 16-bit, stereo, `wMaxPacketSize=192`,
  `bInterval=1`;
- `CFG_TUD_AUDIO_ENABLE_FEEDBACK_EP = 0`: **no feedback endpoint**, so the host
  free-runs 48000 samples/s off its own crystal;
- the consumer is PWM slice 7, `48 MHz / 1000`, derived from the RP2040's
  12 MHz crystal;
- there is **no asynchronous sample-rate conversion (ASRC)** between them and no
  drift counter.

```
  ppm    time to hit    glitch rate      glitches in 600s
         the limit      (samples/s)
  -100   427 s          4.80             832
  -50    853 s          2.40             0
  -25    1707 s         1.20             0
  100    427 s          4.80             832

  Ring depth = 4096 samples = 85.3 ms of buffering
```

Typical crystal errors of 10–50 ppm mean the buffer **saturates after roughly
14–28 minutes, then drops/underflows continuously at 0.5–2.4 events/s** (each
one a sample-level discontinuity = a light click). Two free-running crystals
never re-align themselves, so this is a "guaranteed after a while" problem.

### L5 PLL/PDM RF layer (P1)

The audio DSP decides *what* is sent; this layer decides *how clean* the
emission is. The instantaneous frequency dithers by `step = ref/div` on the
1 MHz PDM tick, and the PLL loop filter only partly averages it:

```
$ python3 tools/pll_range.py check 87900000 75000 1
... div=16 ... PDM step=750.000 kHz
$ python3 tools/pll_range.py check 87900000 75000 2
... div=16 ... PDM step=375.000 kHz
$ python3 tools/pll_range.py check 7074000 3000 1
... div=150 ... PDM step=80.000 kHz
$ python3 tools/pll_range.py check 7074000 3000 2
... div=200 ... PDM step=30.000 kHz
```

`refdiv 2` halves the step on **every** band, but `main.py` currently sets
`refdiv 2` only for targets above 150 MHz (UHF harmonics). Note that the
host cannot compute the **residual** ripple after loop filtering (that needs the
loop bandwidth and a phase-noise measurement), so only the computable step is
given here; the residual is left to hardware measurement.

### L2 ring-buffer observability (P2)

- on an empty ring `fm_pwm_wrap_handler()` sets `s_cur_sample = 0` → the
  carrier parks on fc for that tick (a dropout) instead of holding the last
  sample;
- on a full ring the host sample is dropped silently (the code comments say so);
- **neither event increments any counter**; `diag` exposes only `ISR ticks` and
  `RX frames`, so L1's drift is invisible.

### L6 USB control surface & defaults (P2)

`fm_db256_to_gain()` maps the UAC1 dB value **linearly** to a linear gain:

```c
return (uint16_t)(((clamped - FM_VOL_MIN) * 32767) / (FM_VOL_MAX - FM_VOL_MIN));
```

so `gain_lin = (db + 60) / 60` and the actual attenuation is
`20·log10((db+60)/60)`:

| requested (dB) | actual (dB) | error |
|---|---|---|
| −6 | −0.92 | +5.1 |
| −12 | −1.94 | +10.1 |
| −20 | −3.52 | +16.5 |
| −30 | −6.02 | +24.0 |
| −40 | −9.54 | +30.5 |
| −50 | −15.56 | +34.4 |
| −60 | −∞ | — |

i.e. only the top ~10 dB of the volume slider is accurate.

The other defaults (`DEFAULT_PREEMPH="on"(75us)`, `DEFAULT_SQUELCH=0`,
`DEFAULT_CARRIER=87.9 MHz`) are reasonable in themselves; the problem is the
`refdiv` choice rule in the `band` presets (see L5).

### L4 modulation mapping (negligible)

`freq = s_carrier_hz + ((int64_t)sample * deviation) >> 15`: full-scale ±32767
gives 0.99997× deviation and 0 gives an exactly parked carrier — both
intentional and correct. FM needs no pre-integration of the audio. 48 kHz
sampling gives 3.2× oversampling for a 15 kHz audio band, which is enough.

---

## 3. Full baseline output

<details>
<summary>python3 tools/audio_quality.py resp</summary>

```
Frequency response (dB re input; small signal, limiter not engaged)
  f (Hz)  75us      50us      300us     off       nfm300    lp2only
  20      0.04      0.01      0.35      -0.00     -20.41    -0.00
  50      0.10      0.05      1.25      -0.00     -14.63    -0.00
  100     0.29      0.12      3.34      -0.00     -6.81     -0.00
  300     1.86      0.85      9.53      -0.00     6.28      -0.01
  1000    7.75      5.01      14.24     -0.00     12.82     -0.03
  2000    11.33     8.87      14.91     -0.00     11.62     -0.11
  3000    12.62     10.71     14.94     -0.00     9.05      -0.25
  5000    13.20     11.99     14.61     -0.00     3.84      -0.66
  8000    12.76     11.93     13.79     -0.00     -2.28     -1.51
  10000   12.21     11.49     13.14     -0.00     -5.29     -2.16
  12000   11.63     10.96     12.51     -0.00     -7.67     -2.80
  14000   11.06     10.44     11.91     -0.00     -9.54     -3.39
  15000   10.80     10.18     11.64     -0.00     -10.31    -3.67
  16000   10.56     9.96      11.39     -0.00     -10.98    -3.92
  18000   10.14     9.56      10.97     -0.00     -12.05    -4.35
  20000   9.84      9.27      10.65     -0.00     -12.78    -4.65
  22000   9.65      9.08      10.46     -0.00     -13.22    -4.85
  23000   9.61      9.04      10.41     0.00      -13.32    -4.89

  Net received response = transmit response - standard de-emphasis
  f (Hz)     75us net   50us net  300us net
  20             0.04       0.01       0.35
  50             0.10       0.04       1.21
  100            0.28       0.12       3.19
  300            1.78       0.81       8.33
  1000           6.88       4.61       7.66
  2000           8.57       7.42       3.09
  3000           7.85       7.95      -0.25
  5000           5.04       6.59      -4.93
  8000           0.94       3.29     -9.80
  10000         -1.44       1.13    -12.37
  12000         -3.56      -0.87    -14.59
  14000         -5.43      -2.65    -16.52
  15000         -6.27      -3.47    -17.40
  16000         -7.07      -4.24    -18.20
  18000         -8.49      -5.62    -19.65
  20000         -9.69      -6.81    -20.88
  22000        -10.70      -7.80    -21.90
  23000        -11.13      -8.22    -22.33
```
</details>

<details>
<summary>python3 tools/audio_quality.py thd / limiter / drift</summary>

```
Harmonic distortion (THD, %), 1 s window, harmonics 2..10
  tone       level        preemph 75   preemph off  clips(75us)
  1000       linear       0.033        0.013        0
  1000       loud         17.859       0.002        0
  1000       over         28.778       0.822        0
  5000       linear       0.005        0.004        0
  5000       loud         25.945       0.000        0
  5000       over         28.374       0.545        0
  10000      linear       0.006        0.000        0
  10000      loud         1.946        0.000        0
  10000      over         0.758        0.268        0

Limiter / peak deviation
  signal                       peak_out   peak/dev      clips
  full-scale 1kHz sine [+pre]      29491     0.9000          0
  full-scale 1kHz sine [flat]      32768     1.0000          0
  full-scale 6kHz sine [+pre]      29479     0.8996          0
  full-scale 6kHz sine [flat]      32768     1.0000          0
  full-scale 12kHz sine [+pre]     27336     0.8342          0
  full-scale 12kHz sine [flat]     32768     1.0000          0
  step 0->30000 [+pre]       overshoot=200 samples above knee, clips=0
  step 0->30000 [flat]       overshoot=200 samples above knee, clips=0

Asynchronous sample-clock drift (see the L1 table)
```
</details>

---

## 4. Fix order and expected gain

| Order | Change | Expected gain | Host-side check |
|---|---|---|---|
| 1 | L3: standard 75/50/300 µs pre-emphasis (first-difference form) | net received response within ±1 dB from 0.3–15 kHz; 1 kHz loud-level THD from 17.9 % to <0.1 % | `audio_quality.py resp/thd` before/after |
| 2 | L3: second-order Butterworth band-limit (15 kHz / 3 kHz) | 10 kHz droop from −2.2 dB to <−0.5 dB; 23 kHz stopband from −4.9 dB to <−20 dB | same |
| 3 | L3: keep the band-limit when pre-emphasis is off | removes the hidden "pre-emphasis off = no band-limit" path | same |
| 4 | L1/L2: hold the last sample on underflow + drop/underflow counters | click amplitude goes from "jump to zero" to "hold"; `diag` can see the drift | `audio_quality.py drift` + counter semantics |
| 5 | L5: default `refdiv 2` on every band | halves the PDM step (RF noise floor) | `tools/pll_range.py check` |
| 6 | L6: real dB mapping for the UAC1 volume | accurate volume slider over the whole range | tabulated mapping comparison |
| — | limiter look-ahead | less over-deviation distortion (programme-dependent) | `limiter` overshoot count |

---

## 5. Out of scope

- **Stereo MPX (19 kHz pilot + 38 kHz DSB)**: at 48 kHz sampling it needs 4×
  interpolation (192 kHz), while the current ISR already costs 2–6 µs/sample;
  4× is 40–115 % CPU. It is a separate project requiring a sampling-clock
  redesign.
- **AM/SSB/DRM**: this chain is a fixed-amplitude square wave with frequency-only
  modulation; they need an external linear modulator stage.
- **RF measurements** (phase noise, residual ripple, real received audio): this
  goal explicitly excludes hardware measurement; the "residual ripple" in item 5
  above can only be settled by measurement.
