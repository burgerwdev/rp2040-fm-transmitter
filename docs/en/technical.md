# Technical Architecture

> [中文](../zh/technical.md) · English

## System architecture

```
PC (48 kHz stereo 16-bit PCM)
  → USB UAC1 audio RX callback (TinyUSB / tud_task soft IRQ)
  → stereo→mono mix + volume + mute
  → 15 kHz band-limit + 75 µs pre-emphasis + limiter (fixed-point, no FPU)
  → SPSC ring buffer (4096 samples)
  → PWM slice 7 wrap IRQ @ exactly 48 kHz (48 MHz / 1000, top NVIC priority)
  → pico_fractional_pll_set_freq_u32(carrier + sample·dev/32768)
  → Core1 1 MHz PDM dithers PLL_SYS fbdiv_int
  → CLK_GPOUT0 → GPIO21, ~87.9 MHz out
```

- The hard-real-time audio path never passes through Python; MicroPython only
  configures and controls it.
- clk_sys is fixed at **48 MHz** (a hard requirement of pico-fractional-pll:
  clk_sys from PLL_USB, PLL_SYS dedicated to the RF output).

## Key engineering points

### 1. USB audio (UAC1)

- `CFG_TUD_AUDIO` is enabled in the built-in TinyUSB config; the configuration
  descriptor carries a UAC1 block (IAD + AC + AS alt0/alt1, 48 kHz stereo
  16-bit, ISO OUT EP 0x03, synchronous);
- Volume/mute control requests are handled in `tud_audio_*_req_entity_cb`;
- The audio callbacks must be compiled in: `#include "tusb.h"` has to come
  before `#if CFG_TUD_AUDIO`.

### 2. 48 kHz sample clock

- PWM slice 7 (TOP=999, div 1.0) provides an exact jitter-free 48 kHz timebase
  (the µs timer cannot do exact 48 kHz);
- **PWM_IRQ_WRAP must run at the top NVIC priority (0)**: at the default
  priority it was starved by the USB IRQ (only ~26 kHz, audio slowed +
  distorted);
- **No 64-bit division in the ISR**: `acc = (freq−low)·k >> 32` with
  `k = 2⁶⁴/Δf` precomputed at init (M0+ has no FPU; division is very slow).

### 3. Core1 PDM and clocks

- The library's Core1 loop dithers the PLL feedback divider at 1 MHz (PDM) and
  requires clk_sys = 48 MHz;
- MicroPython's linker gives SCRATCH_X 0 bytes, so the library's
  `multicore_launch_core1` (which needs `.stack1`) panics — we use
  `multicore_launch_core1_with_stack` with our own 2 KB stack in plain RAM;
- Do not use `_thread` together with `pico_fm` (both need Core1).

### 4. Diagnostics

- `diag`: ISR ticks and RX frames over 1 s (both should be ~48000/s);
- `pwm`: clk_sys, PWM registers, ISR cost (2–6 µs is normal);
- `pll`: PLL ready / actual output range / last written frequency;
- `ring`: ring fill % (near 0% when balanced; 99% means the consumer stalled).

## How the build works

`build.sh` clones a clean MicroPython (tries the maintainer's fork
`git.sr.ht/~bytewolf/micropython` first, then upstream) → applies
`patches/micropython-fm.patch` (skips if already applied) → `make submodules`
→ `make BOARD=RPI_PICO_FM`. The patch contains:
- `ports/rp2/boards/RPI_PICO_FM/` (new board: 48 MHz clock, USB_AUDIO, FM macros,
  unique VID/PID 0x1209:0xFA50, USB strings);
- `ports/rp2/fm_transmitter/` (pico_fm user C module: **the bundled
  pico-fractional-pll source** (BSD-3-Clause, Kazuhisa Terasaki) + modulator +
  MicroPython module);
- `ports/rp2/main.c` (48 MHz boot), `modmachine.c` (disable machine.freq setter);
- `shared/tinyusb/` (UAC1 descriptors and config).

[kaduhi/pico-playground (fm_transmitter branch)](https://github.com/kaduhi/pico-playground)
is reference only (UAC1 descriptor layout and FM modulation ideas); no code
from it is included.

### Build prerequisites (Ubuntu/Debian)

`build.sh` is a single automated command, but the host must have the RP2040
cross-toolchain installed. On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y \
    build-essential git \
    cmake \
    gcc-arm-none-eabi \
    libnewlib-arm-none-eabi \
    libstdc++-arm-none-eabi-newlib \
    python3
```

- `build-essential` — `make`, `gcc` and friends (drives the build);
- `git` — `build.sh` clones MicroPython (required, no tarball fallback);
- `cmake` — the pico-sdk build system;
- `gcc-arm-none-eabi` — the ARM Cortex-M0+ cross compiler (>= 10 is fine);
- `libnewlib-arm-none-eabi` + `libstdc++-arm-none-eabi-newlib` — newlib C
  library / C++ runtime for the target, required by the pico-sdk;
- `python3` — used by MicroPython's build scripts.

Optional, only for uploading/flashing (not needed by `build.sh` itself):
`mpremote` (`pip install mpremote`, upload `main.py`) and `picotool`
(`tools/flash.sh`). Check the toolchain with
`arm-none-eabi-gcc --version` (expect 10.3.x or newer).

## Regenerating the patch

```bash
cd micropython
git add -A
git diff --cached > ../release/patches/micropython-fm.patch
git reset -q
```
