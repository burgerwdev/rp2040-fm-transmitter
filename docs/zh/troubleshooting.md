# 故障排查

> 中文 · [English](../en/troubleshooting.md)

## Windows：设备带感叹号 / 没有声卡

**原因**：Windows 按 VID/PID 缓存驱动配置。旧固件（或之前的 MicroPython）用
`0x2E8A:0x0005`（纯 CDC），新固件是 CDC+Audio 复合设备且换用独立身份
`0x1209:0xFA50`。若仍出现旧身份缓存冲突：

1. 设备管理器 → 查看 → 显示隐藏的设备；
2. 卸载带感叹号的旧设备（勾选"删除驱动程序软件"）；
3. 重烧最新固件、重插。

## 频谱仪/收音机没有信号

1. 确认控制台已进入（`fm>` 出现），`status` 显示 `PLL ready: yes` 与
   `RF output: ON`；
2. `diag`：ISR/RX 都应为 ~48000/s；若 ISR 明显偏低（如 26000），说明采样中断
   被饿死——固件必须是包含"最高中断优先级"修复的版本；
3. `sweep 87000000 88500000 500000` 看频谱仪信号是否移动（PLL/Core1 自检）；
4. 频谱仪探头直接搭 **GPIO21**（高阻探头即可见 ~87.9MHz + 谐波）；
5. 收音机贴近 Pico 调到 87.9MHz（±100kHz 刻度误差常见）。

## 控制台进不去

1. 确认 `main.py` 在板子文件系统根目录（REPL 里 `import os; os.listdir('/')`）；
2. 用最新版 `main.py`（旧版会被 Thonny 连接时的 Ctrl-C 打断）；
3. 拔插后等 2~3 秒再连串口（USB 枚举 + 横幅等待终端）；
4. 用 `screen`/PuTTY/minicom 比 Thonny 更可靠；Thonny 连接后别再点 Run。

## 声音异常（失真/变速/断续）

- `diag` 确认 ISR ≈48000/s（低于此 = 中断饿死，更新固件）；
- `pre off` 对比（预加重+限幅过激可能削波）；`dev 40000` 收窄频偏；
- 电脑音量 60~80%，避免持续顶限幅器；
- 长时间播放后出现咔嗒：USB 主机与 PWM 采样时钟不同源，本固件已改为
  「欠载重复上一采样」并计数，用 `diag` 看 Underflows/Drops 增量即可判断（增量为
  0 表示两颗晶振刚好匹配）；计数持续增长说明存在 ppm 级漂移——听感已改善，
  但彻底消除需要 ASRC 或 UAC1 反馈端点。

## 上传脚本找不到板子 / 选错了串口

机上同时插了多块 ttyACM 设备（如 ADALM-Pluto 自带的串口控制台）时，
`tools/upload.sh` / `tools/serial.sh` 会按 USB ID **1209:fa50** 定位开发板，
不会误选。手动指定：

```bash
FM_PORT=/dev/ttyACM1 tools/upload.sh
./tools/serial.sh /dev/ttyACM1
tools/pico_port.sh                 # 只打印解析到的端口，或列出候选设备
```

报 `could not enter raw repl` 且捕获到别的登录提示或乱码，通常就是选错了
端口（用 `tools/pico_port.sh` 确认）；另外 BOOTSEL 模式下没有 CDC 端口。

## 常见误用

- **GPIO21 接天线**：违法且有干扰风险，严禁；
- **`audio off` 当静音用**：它会冻结输出频率，静音请用 `mute`；
- **`freq` 超出当前 PLL 范围**：会重启到该载波（等价 reinit），不是报错；
- **`power` 用高阻探头看幅度**：看不出差异，需 50Ω 负载。
