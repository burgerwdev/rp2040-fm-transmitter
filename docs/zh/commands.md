# 指令使用指南

> 中文 · [English](../en/commands.md)

`fm>` 控制台指令（值默认单位 Hz）。控制台可通过 `help` 随时查看。

## 常用指令

| 指令 | 说明 | 示例 |
|---|---|---|
| `help` | 显示全部指令帮助 | `help` |
| `ver` | 显示版本号、固件 sha256 与项目链接 | `ver` |
| `status` / `s` | 显示全部音频/FM 参数 | `status` |
| `freq <Hz>` | 设置调制中心（当前 PLL 范围内实时生效；范围外自动重启到该载波） | `freq 88000000` |
| `dev <Hz>` | 设置全幅频偏（1000..PLL 范围一半） | `dev 40000` |
| `reinit <载波> <频偏> [引脚]` | 保存新载波/频偏/引脚并重启 | `reinit 98000000 50000 21` |
| `pin <21\|23\|24\|25>` | 切换 RF 输出引脚（保存并重启） | `pin 21` |
| `power <2\|4\|8\|12>` | RF 驱动强度 mA（发射功率）；8/12 偶次谐波抑制更好 | `power 12` |
| `out on\|off` | RF 输出开关 | `out on` |
| `audio on\|off` | USB 音频→FM 路由开关 | `audio on` |
| `vol <0-100>` | 音量百分比 | `vol 70` |
| `mute on\|off` | 静音（≈vol 0；audio off ≠ mute） | `mute off` |
| `pre on\|off` | 15kHz 带限 + 75µs 预加重 + 限幅 | `pre on` |
| `led <0\|1\|2\|3>` | LED：0关 1常亮 2发射时亮 3随音频VU | `led 3` |
| `ledpin <0-29>` | 设置普通 LED 引脚（保存并重启） | `ledpin 25` |
| `ledpin ws2812 [引脚]` | 使用 WS2812 彩灯（默认 GPIO16） | `ledpin ws2812` |
| `vbar` | 单行音频电平条（~50fps，任意键退出） | `vbar` |
| `ring` | 环形缓冲水位 % | `ring` |
| `diag` | 1 秒内实测 ISR/RX 速率（应各 ~48000） | `diag` |
| `pwm` | PWM/ISR 诊断 | `pwm` |
| `pll` | PLL 诊断（ready/范围/最后写入频率） | `pll` |
| `sweep [低 高 步长]` | 暂停音频扫频（PLL 自检） | `sweep 87000000 88500000 500000` |
| `reset` | 删除保存的配置并重启到默认 | `reset` |
| `exit` | 退出控制台回到 REPL | `exit` |

## 说明

- **`freq` 与 PLL 范围**：`freq` 只能在当前 `init` 锁定的 PLL 范围内实时微调
  （`status` 显示实际范围，如 87.750..88.500 MHz）；换频段用 `reinit`。
- **`power`（发射功率）**：RP2040 GPIO 是轨到轨推挽，用高阻探头测幅度看不出
  区别；接 50Ω 真实负载（频谱仪 50Ω 输入 + 衰减器）才见 10dB+ 差异。
- **`audio off` ≠ `mute`**：`audio off` 停止调制器，输出冻结在最后频率；
  想安静发射载波用 `mute` 或 `vol 0`。
- **`reset`**：删除 `/fm_cfg.json`（载波/频偏/引脚/功率/LED）并重启。
- 配置持久化在板内文件系统 `/fm_cfg.json`；开机自动恢复。
