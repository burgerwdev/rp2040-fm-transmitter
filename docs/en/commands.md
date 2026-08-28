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
| `pin <21\|23\|24\|25>` | Switch the RF output pin (saves and reboots) | `pin 21` |
| `power <2\|4\|8\|12>` | RF drive strength in mA (transmit power); 8/12 give better even-harmonic suppression | `power 12` |
| `out on\|off` | RF output on/off | `out on` |
| `audio on\|off` | USB audio → FM routing on/off | `audio on` |
| `vol <0-100>` | Volume in percent | `vol 70` |
| `mute on\|off` | Mute (≈ vol 0; audio off ≠ mute) | `mute off` |
| `pre on\|off` | 15kHz band-limit + 75µs pre-emphasis + limiter | `pre on` |
| `led <0\|1\|2\|3>` | LED: 0=off 1=always-on 2=on-while-streaming 3=audio VU | `led 3` |
| `ledpin <0-29>` | Set the plain-LED pin (saves and reboots) | `ledpin 25` |
| `ledpin ws2812 [pin]` | Use a WS2812 NeoPixel (default GPIO16) | `ledpin ws2812` |
| `vbar` | Single-line audio level bar (~50fps, any key exits) | `vbar` |
| `ring` | Ring buffer fill % | `ring` |
| `diag` | Measured ISR/RX rates over 1 s (both ~48000) | `diag` |
| `pwm` | PWM/ISR diagnostics | `pwm` |
| `pll` | PLL diagnostics (ready/range/last written freq) | `pll` |
| `sweep [lo hi step]` | Pause audio and sweep the carrier (PLL self-test) | `sweep 87000000 88500000 500000` |
| `reset` | Delete the saved config and reboot to defaults | `reset` |
| `exit` | Leave the console back to the REPL | `exit` |

## Notes

- **`freq` vs PLL range**: fine-tunes live only inside the current PLL range
  (shown by `status`); use `reinit` to switch bands.
- **`power`**: the RP2040 GPIO is rail-to-rail; a high-impedance probe sees no
  amplitude change. Use a real 50Ω load (SA 50Ω input + attenuator) to see a
  10 dB+ difference.
- **`audio off` ≠ `mute`**: `audio off` freezes the modulator at the last
  frequency; use `mute`/`vol 0` for a quiet carrier.
- **`reset`** deletes `/fm_cfg.json` and reboots to defaults.
- Settings persist in `/fm_cfg.json` on the board's filesystem and are restored
  at boot.
