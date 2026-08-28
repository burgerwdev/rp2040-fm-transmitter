# 使用说明

> 中文 · [English](../en/usage.md)

## 烧录

1. 按住 **BOOTSEL** 按钮的同时把 Pico 插入电脑 USB，出现 `RPI-RP2` 盘；
2. 把 `firmware/rp2040pico_fm_firmware.uf2` 拖入该盘（自动烧录并重启）；
   或 `tools/flash.sh`（需 picotool）。
3. 校验（可选）：`sha256sum -c firmware/sha256.txt`。

## 连接

- 设备会枚举为：**串口**（MicroPython REPL / `fm>` 控制台）+ **USB 音频设备**
  （"Pico FM Sound Card"，系统里显示为 RP2040 Pico RF Transmitter / BurgerW）。
- Linux 用 `screen /dev/ttyACM0 115200`；Windows 用 PuTTY（COM 口 115200）；
  Thonny 也可直接连接。
- 上传控制台脚本（只需一次）：`mpremote cp python/main.py :main.py`。
- 之后拔插或按 RESET，等待 2~3 秒，打开串口即自动进入 `fm>` 控制台。

## 播放

1. 在电脑声音设置中把输出设备选为 **Pico FM Sound Card**；
2. 播放音乐；
3. FM 收音机贴近 Pico，调到 **87.9MHz**。

> 提示：默认开启 75µs 预加重（与收音机去加重匹配），声音更清晰；若觉得失真，
> 可 `pre off` 对比、`dev 40000` 收窄频偏、或把电脑音量降到 60~80%。

## 指示灯

- 默认（普通 LED 板）`led 3`：随音频电平闪动（VU）；`led 0/1/2`：关/常亮/发射时常亮；
- 板载灯不是 GPIO25 的板子（如 RP2040-Zero 的 WS2812 在 GPIO16）：
  `ledpin ws2812` 启用彩灯模式（`led 3` = 绿→黄→红 VU），或接普通 LED 后用
  `ledpin <gpio>` 指定。

## 切换频段

`reinit 98000000 50000`（载波/频偏，可加第三个参数换引脚）→ 保存并重启生效；
`freq <Hz>` 在当前 PLL 范围内实时微调，范围外自动重启到该载波。
