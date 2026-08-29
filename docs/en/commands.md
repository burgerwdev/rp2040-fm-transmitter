# Console Command Reference

> [中文](../zh/commands.md) · English

All values are in Hz unless noted. Type `help` in the console anytime.

## Common commands

| Command | Description | Example |
|---|---|---|
| `help` | Show the full command help | `help` |
| `ver` | Show version, firmware sha256 and project links | `ver` |
| `status` / `s` | Show all audio/FM parameters | `status` |
| `freq <Hz>` | Set the modulation centre (live inside the current PLL range; reboots to that carrier outside it) | `freq 88000000` |
| `dev <Hz>` | Set full-scale deviation (1000..half the PLL range) | `dev 40000` |
| `reinit <carrier> <dev> [pin]` | Save new carrier/deviation/pin and reboot | `reinit 98000000 50000 21` |
| `pdm <1\|2\|3\|4>` | PDM dither rate in MHz (1 = default; 2+ lowers narrowband noise; saves and reboots) | `pdm 2` |
| `refdiv <1\|2>` | PLL reference divider (2 = half PDM step, ~6dB less narrowband noise; saves and reboots) | `refdiv 2` |
| `pin <21\|23\|24\|25>` | Switch the RF output pin (saves and reboots) | `pin 21` |
| `pwr <2\|4\|8\|12>` | RF drive strength in mA (transmit power); 8/12 give better even-harmonic suppression | `pwr 12` |
| `rf on\|off` | RF output on/off | `rf on` |
| `audio on\|off` | USB audio → FM routing on/off | `audio on` |
| `vol <0-100>` | Volume in percent | `vol 70` |
| `mute on\|off` | Mute (≈ vol 0; audio off ≠ mute) | `mute off` |
| `pre on\|off\|50\|75` | 15kHz band-limit + pre-emphasis (50 or 75µs) + limiter; on = 75µs (default), off = no processing | `pre 50` |
| `sq <0-100>` | Mute weak samples below this % of full scale so faint background noise is not modulated; paused/muted audio parks the carrier on fc and the radio is silent anyway; 0 = off (default) | `sq 5` |
| `led <0\|1\|2\|3>` | LED: 0=off 1=always-on 2=on-while-streaming 3=audio VU | `led 3` |
| `ledpin <0-29>` | Set the plain-LED pin (saves and reboots) | `ledpin 25` |
| `ledpin ws2812 [pin]` | Use a WS2812 NeoPixel (default GPIO16) | `ledpin ws2812` |
| `vbar` | Audio level meter (~50fps): peak bar, squelch threshold (T marker) and clip rate; shows "silent" (slow refresh) while the input is quiet; any key exits | `vbar` |
| `ring` | Ring buffer fill % | `ring` |
| `diag` | Measured ISR/RX rates over 1 s (both ~48000) | `diag` |
| `pwm` | PWM/ISR diagnostics | `pwm` |
| `pll` | PLL diagnostics (ready/range/last written freq) | `pll` |
| `sweep [lo hi step]` | Pause audio and sweep the carrier (PLL self-test) | `sweep 87000000 88500000 500000` |
| `cls` | Clear the terminal screen (ANSI) | `cls` |
| `reset` | Delete the saved config and reboot to defaults | `reset` |
| `reboot` | Save the current settings and reboot (same as replugging, keeps config) | `reboot` |
| `exit` | Leave the console back to the REPL | `exit` |

## Notes

- **`freq` vs PLL range**: fine-tunes live only inside the current PLL range
  (shown by `status`); use `reinit` to switch bands.
- **`power`**: the RP2040 GPIO is rail-to-rail; a high-impedance probe sees no
  amplitude change. Use a real 50Ω load (SA 50Ω input + attenuator) to see a
  10 dB+ difference.
- **`audio off` vs `mute`**: both silence the radio immediately (the carrier
  parks on fc, no modulation). `audio off` stops the USB-audio -> FM routing
  and is reported as "muted" to the host (Windows shows the mute state);
  `mute` keeps the routing but silences the stream. `vol 0` is another way to
  get a quiet carrier.
- **`pre`**: on = 75µs (US/EU broadcast), `50` = 50µs (China/Japan). If your
  radio's de-emphasis is 50µs, `pre 50` reduces the high-frequency hiss.
- **Narrowband reception (handheld radios)**: the fractional PLL dithered the
  PDM at 1 MHz leaves a residual frequency ripple that broadcast FM (75kHz
  deviation, 230kHz IF) masks but narrowband FM (2.5-5kHz deviation,
  12.5/25kHz channels) hears as noise.  For a handheld radio use the 2m band
  (144-148MHz, `reinit 145000000 12000 21`), switch the radio to WIDE (25kHz)
  and keep the deviation at ~10-12kHz for the least noisy result.  The
  experimental `pdm 2` (faster dither, more PLL averaging) and `refdiv 2`
  (half the PDM step) reduce that ripple by ~6-18dB in total and may make
  12.5kHz narrowband usable - test on hardware, defaults are unchanged.
- **`sq` (weak-sample mute)**: samples below the threshold are zeroed in the
  RX path, so faint stream noise is not modulated. Note that pausing or
  muting already parks the carrier exactly on fc (no modulation), which makes
  a radio tuned to fc silent — the off-tune "silence gate" from earlier
  versions was removed because a CW at the IF band edge can make receiver
  AGC/quieting cycle audibly. Watch the T marker in `vbar`.
- **`vbar` clips**: the per-second clip rate shows how often the limiter is
  engaged; a high rate means the PC volume is too high (lower it, or `vol`).
- **`reset`** deletes `/fm_cfg.json` and reboots to defaults (75µs pre-emphasis,
  squelch off). **`reboot`** persists the current settings first and reboots
  without deleting them — use it when a change only takes effect after a
  replug (e.g. after `ledpin`/`pin`-style reconfigurations you have already
  saved).
- Settings persist in `/fm_cfg.json` on the board's filesystem and are restored
  at boot.
