# Troubleshooting

> [中文](../zh/troubleshooting.md) · English

## Windows: device with an exclamation mark / no sound card

**Cause**: Windows caches driver profiles per VID/PID. The stock MicroPython
PID (0x2E8A:0x0005, CDC-only) conflicts with the CDC+Audio composite — this
firmware uses a unique identity (0x1209:0xFA50). If leftover devices appear:

1. Device Manager → View → Show hidden devices;
2. Uninstall the error devices ("delete driver software");
3. Re-flash and replug.

## No RF signal

1. Confirm the `fm>` console is up; `status` shows `PLL ready: yes` and
   `RF output: ON`;
2. `diag`: ISR/RX should both be ~48000/s; if the ISR is ~26000/s, the sample
   IRQ was starved — flash a build with the top-priority IRQ fix;
3. `sweep 87000000 88500000 500000` — the SA signal should move (PLL/Core1
   self-test);
4. Probe **GPIO21** directly (a high-Z probe sees ~87.9 MHz + harmonics);
5. Put the radio next to the Pico, tuned to 87.9 MHz (dial error of ±100 kHz
   is common).

## Console does not appear

1. Make sure `main.py` is at the root of the board filesystem
   (`import os; os.listdir('/')` in the REPL);
2. Use the latest `main.py` (old versions were killed by Thonny's Ctrl-C);
3. After replug wait 2–3 s before connecting (USB enumeration + banner wait);
4. `screen`/PuTTY/minicom are more reliable than Thonny; don't press Run in
   Thonny after the console is up.

## Bad audio (distortion / wrong speed / dropouts)

- Run `diag` — the ISR must be ≈48000/s (lower = starved IRQ, update firmware);
- A/B with `pre off`; try `dev 40000`;
- Keep the PC volume at 60–80% to avoid constant limiting;
- Occasional clicks come from host/local clock drift in synchronous USB audio —
  expected.

## Common mistakes

- **Antenna on GPIO21**: illegal and noisy — never;
- **`audio off` as mute**: it freezes the output frequency; use `mute`;
- **`freq` outside the current PLL range**: reboots to that carrier (like
  reinit), not an error;
- **`power` measured with a high-Z probe**: shows no difference; use a 50Ω load.
