# Usage Guide

> [中文](../zh/usage.md) · English

## Flashing

1. Hold **BOOTSEL** while plugging the Pico into USB — an `RPI-RP2` drive
   appears.
2. Drag `firmware/rp2040pico_fm_firmware.uf2` onto it (auto-flash & reboot),
   or run `tools/flash.sh` (needs picotool).
3. Optional check: `sha256sum -c firmware/sha256.txt`.

## Connecting

- The device enumerates as a **serial port** (MicroPython REPL / `fm>` console)
  plus a **USB audio device** ("Pico FM Sound Card"; shown as
  RP2040 Pico RF Transmitter / BurgerW).
- Linux: `screen /dev/ttyACM0 115200`; Windows: PuTTY (COM port, 115200);
  Thonny also works.
- Upload the console script once: `mpremote cp python/main.py :main.py`.
- Afterwards, replug (or press RESET), wait 2-3 s, open the serial terminal —
  the `fm>` console appears automatically.

## Playing

1. In the OS sound settings, select **Pico FM Sound Card** as the output.
2. Play something.
3. Put an FM radio next to the Pico, tuned to **87.9 MHz**.

> Tip: 75µs pre-emphasis is on by default (matches the radio's de-emphasis).
> If it sounds distorted, try `pre off`, `dev 40000`, or lower the PC volume.

## LED

- Default (plain-LED boards) `led 3`: audio-level VU; `led 0/1/2`: off /
  always-on / on-while-streaming.
- Boards without an LED on GPIO25 (e.g. RP2040-Zero's WS2812 on GPIO16):
  `ledpin ws2812` enables NeoPixel mode (`led 3` = green→yellow→red VU), or
  attach a plain LED and set its pin with `ledpin <gpio>`.

## Changing band

`reinit 98000000 50000` (carrier / deviation, optional 3rd arg = RF pin) saves
and reboots; `freq <Hz>` fine-tunes live inside the current PLL range, and
reboots to the target carrier when outside it.
