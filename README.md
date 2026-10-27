# ESP Reset

把 ESP32 变成独立的网络复位按键：配网后打开设备自己的网页，即可短暂接通外部电子开关，模拟按下另一块开发板的 RESET。复位模块提取自 [Ken-u/esphook](https://github.com/Ken-u/esphook) 的复位功能，已去掉显示屏、LVGL、字库、蜂鸣器、Agent Hook 和主机 daemon 依赖。

## 支持的板子

| 板子 | 构建目标 | 默认三路输出（1 / 2 / 3） | 板载恢复键 | Flash |
| --- | --- | --- | --- | --- |
| ESP32-S3 公版 N16R8 / DevKitC 类板 | `esp32s3` | **GPIO4 / GPIO5 / GPIO6** | BOOT / GPIO0 | 16 MB |
| ESP32-C3 SuperMini | `esp32c3` | **GPIO3 / GPIO4 / GPIO5** | BOOT / GPIO9 | 4 MB |

GPIO 是芯片编号，以板上丝印为准。**C3 已按此板的限制禁用 GPIO20/21 复位输出，旧版 GPIO20 接线必须迁移到 GPIO3/4/5。** **S3 不要照旧 C3 图使用 GPIO20：S3 的 GPIO19/20 是原生 USB。** N16R8 的 8 MB PSRAM 无需使用，本项目不初始化 PSRAM；GPIO26–37 的内存引脚也不会用作复位输出。该功能只需片上 RAM。两种固件不可混刷。

## 功能

- USB 配网，固定管理密码 `esp32reset`，不启动 AP；Wi-Fi 凭据保存在 NVS。
- macOS / Linux 一键脚本使用 uv 准备 Python，无需 ESP-IDF 或编译环境。
- 设备网页：网络/固件/复位状态、目标板复位、Wi-Fi 设置、应用 OTA、控制器重启。
- 一块板支持三路独立复位；每路约 200 ms 高电平脉冲、松开后独立冷却 3 秒，并分别统计次数。没有全路复位或自动反复复位功能。
- 管理密码鉴权；API 和 CLI；同一启动内每路最近 8 个成功 request_id 独立去重。
- USB 完整刷写包、独立 OTA 应用镜像；CI 构建 C3 和 S3 两个版本。

## 硬件先接对

**GPIO 不是干接点，不能直接跨接未知按键。** 按上表分别用三个 GPIO 驱动三个外部高电平有效的 MOSFET / PhotoMOS 开关。每路需要自己的一套开关及电阻，不能把三个输出直接并在一起。

接地触发的 RESET 按键可以用 2N7002 + 330 Ω + 4.7 kΩ。接线与实体脚位见 [docs/hardware.md](docs/hardware.md) 和 [接线图](docs/reset-wiring.svg)。如果希望通用地模拟两根未知按键线，选双向输出、3.3 V 高电平触发的隔离 PhotoMOS 模块。

先不要接目标板，验证 ESP 输出空闲低、操作时短暂拉高，再接入目标 RESET。外部下拉必须保留，确保上电、刷机及 ESP 自身重启期间开关保持断开。

## 从构建产物刷机

在仓库 **Actions → 最新成功的 Build and test → Artifacts** 下载对应的：

- `esp-reset-esp32s3-n16r8`
- `esp-reset-esp32c3-supermini`

解压后接 **原生 USB / OTG** 口，关闭其他串口程序，执行：

```bash
sh setup.sh
# 多设备时显式指定，例如 macOS：
sh setup.sh --port /dev/cu.usbmodem5A450470201
```

脚本自动准备 uv、Python 3.12、esptool 5.6.0、pyserial 3.5，读取固件自带地址刷写，隐藏输入 Wi-Fi 密码，等待联网并打印后台网页地址，最后进入自动重连串口监视。默认 115200 刷写，支持 `--baud 460800` 和 `--no-stub`。第一次下载工具和依赖需要互联网。

**后台密码固定 `esp32reset`**，终端也会直接显示。旧随机管理密码升级后失效，Wi-Fi 配置保留。原生 USB 在 ESP 自身 RESET 时断开是重新枚举，脚本会按 USB 序列号/物理位置等待同一设备，并重新查询密码和网页地址；最早的 ROM 日志可能仍早于重连，查询状态不依赖启动日志。USB-UART 桥接口不支持本版 USB 配网协议，两个板型均使用原生 USB。

如需手动下载：按住 BOOT，点 RESET/EN，松开 BOOT 后刷写。刷完若仍在 ROM 下载模式，点 EN（不按 BOOT）运行。Linux 若串口权限不足，需要系统管理员授予串口设备访问权限。

下载包根目录只放 README、SHA256SUMS 和入口脚本；二进制在 `firmware/`，内部 Python 工具在 `tools/`。依赖位于 uv 缓存，缺少 uv 时安装到包内 `.tools/`，不修改 shell 配置。

| 命令 | 用途 |
| --- | --- |
| `sh setup.sh` | 准备环境 → 刷写 → USB 配网 → 看串口 |
| `sh flash.sh` | 仅刷写，打印密码及设备状态 |
| `sh wifi.sh` | 单独 USB 配网，联网后打印网页地址 |
| `sh monitor.sh` | 看串口，RESET 后自动重连 |
| `sh status.sh --url http://ESP的IP` | HTTP 查询状态，无需串口 |
| `sh reset.sh --url http://ESP的IP --channel 1` | HTTP 单路复位，支持 1/2/3 |
| `sh loader.sh --url http://ESP的IP` | HTTP RECOVERY + RESET |
| `sh maskrom.sh --url http://ESP的IP` | HTTP MASKROM + RESET |
| `sh result.sh` | 只查询上一次操作，不按键 |

**日常控制通过 IP + HTTP，不需要把 ESP 接到电脑。** ESP 和目标板按接线图连接并共地（使用隔离开关时按模块要求）。USB 只用于刷机和可选的首次配网。网络命令接受 `--url` 或 `ESP_RESET_URL`；USB 配网成功后也会把地址保存在包目录 `device.json`，后续可省略地址。刷写/USB入口支持 `--port`；`sh run.sh ports` 列串口，`sh run.sh usb-status` 通过USB查状态。

### 网页组合与操作回执

网页新增 Loader / Maskrom 按钮，调用与脚本相同的动作服务。`POST /api/action` 提交 `{boot_id,request_id,mode,channel,hold_ms}`，返回202后由独立任务执行；`GET /api/action?id=...` 查询 queued/running/released/failed，查询不会触发GPIO。`/api/status` 提供本次启动唯一 `boot_id` 及每路 `pulse_count`、`last_request_id`、`pressed_us`、`released_us`，这些是固件GPIO驱动调用记录，不是对外部电压的测量。

网页在请求发出前保存编号；超时后只查询同一编号，刷新页面也保留未确认操作锁。网络脚本把编号保存在 `last-action.json`；未确认时再次运行先查旧请求，不发新请求。同一包同时只能运行一个控制脚本。未知结果不会自动解锁，需确认目标状态后在网页手动解锁，或运行 `sh result.sh --clear-unknown`。检测到ESP启动编号变化时，脚本将旧操作标记为 unknown_after_restart 并解除旧操作锁，本次不触发按键；下一次显式运行才创建新操作。网络仍断开或同一次启动结果未知时继续保留锁。ESP重启后不重放旧动作；每次启动保存最近16个动作回执，不跨重启。这里不宣称已经定位GPIO6导致目标板两次重启的电气/启动原因，需要结合新的执行记录或逻辑分析仪确认。

### Loader / Maskrom 接线

独立三路复位照旧使用 `reset.sh --channel N`。**只有将三路改接同一目标板的三个按键时，才使用 loader / maskrom**：

| 通道 | S3 | C3 | 组合模式用途 |
| --- | --- | --- | --- |
| 1 | GPIO4 | GPIO3 | RESET |
| 2 | GPIO5 | GPIO4 | RECOVERY / Loader 启动键 |
| 3 | GPIO6 | GPIO5 | 专用 MASKROM 按键 |

每路仍使用独立 MOSFET/PhotoMOS 开关，不能直接接未知信号。时序：启动键接通 100ms → RESET 接通 200ms → 松 RESET → 再保持启动键 1500ms → 松启动键。`--hold-ms` 可调最后保持时间（100..10000ms）。组合操作期间其他复位、OTA 和控制器重启互斥，结束后所有通道冷却 3 秒；释放失败的通道锁定直到 ESP 重启。同一启动内缓存最近 8 个成功组合请求，脚本不自动重试操作。

脚本只确认 GPIO 时序已执行，不保证目标板进入特定模式。Loader 依赖目标板 bootloader 对 RECOVERY 的处理；Maskrom 需要目标板提供专用按键或安全控制接口。不要将 eMMC CLK 等信号直接接 ESP 或按通用按键图短接。执行后从目标串口或 `rkdeveloptool ld` 检查模式。

## 正常使用与恢复

网页分别提供“复位第 1 / 2 / 3 路”按钮，显示各路 GPIO、状态和次数；确认后只对选中的一路执行 200 ms 左右脉冲。成功只表示外部开关已松开，不表示目标系统已经启动完成。

控制器运行时长按 **BOOT 约 5 秒**，仅清除 Wi-Fi，之后通过 USB 重新配网，管理密码保持不变。上电就按住 BOOT 会进入 ROM 下载模式；这里指正常运行后再长按。

网页“重启 ESP 控制器”重启的是控制器；目标板复位是另一个按钮。网页 OTA 上传对应芯片的 **`esp-reset.bin`**，不要上传 bootloader、分区表或其他项目的固件。OTA 校验芯片、映像和项目名称；校验失败不切换启动分区。新应用成功初始化网络及网页后才取消回滚。凭据存于 NVS，升级保留。

所有控制在本地 HTTP 上完成，适用于可信局域网。跨网段访问可使用路由/VPN；此独立版本没有原 esphook 的反向主机连接，不能依靠它穿过单向 NAT。不要直接把设备 HTTP 端口暴露到公网。

## CLI 与 API

不需要常驻主机进程，CLI 仅在执行时请求设备：

```bash
python3 tools/esp_reset.py --url http://192.168.1.50 status
python3 tools/esp_reset.py --url http://192.168.1.50 reset --channel 1
python3 tools/esp_reset.py --url http://192.168.1.50 reset --channel 2
python3 tools/esp_reset.py --url http://192.168.1.50 reset --channel 3
python3 tools/esp_reset.py --url http://192.168.1.50 wifi 'MyWiFi'
python3 tools/esp_reset.py --url http://192.168.1.50 ota esp-reset.bin
```

默认管理密码为 `esp32reset`；可通过 `ESP_RESET_TOKEN` 环境变量提供。CLI 不自动重试任何复位请求。超时可能发生在脉冲执行后，先观察目标板状态再决定是否再次操作。

| 接口 | 方法 | 内容 |
| --- | --- | --- |
| `/` | GET | 设备网页 |
| `/api/status` | GET | `channels` 数组包含三路 GPIO / 冷却 / 次数 / 状态，不含密码 |
| `/api/reset` | POST JSON | `{"channel":1,"request_id":"一次操作的唯一ID"}` |
| `/api/wifi` | POST JSON | `{"ssid":"WiFi名","password":"密码"}` |
| `/api/reboot` | POST JSON | `{}`，重启 ESP |
| `/api/ota` | POST binary | 同芯片 `esp-reset.bin` |

除 `/` 外均需请求头 `X-Reset-Token: <管理密码>`。JSON 请求需 `Content-Type: application/json`。复位冷却/维护中返回 409；松开后才返回 `{"ok":true,"state":"released"}`。`channel` **必须明确填写整数 1、2 或 3**，遗漏或非法值返回 400，绝不默认复位某一路。成功回执还包含实际 `channel` 和 `gpio`。request_id 是同次操作的幂等键，去重只覆盖该路本次启动中最近 8 个成功请求，不跨重启、不跨通道、不无限缓存。

从单路 0.1 版升级后请刷新网页并更新 CLI/API 调用；升级保留 Wi-Fi，管理密码改为固定值。原有不带 channel 的请求会被拒绝。

## 从源码构建

使用 **ESP-IDF v5.5.4**：

```bash
source "$IDF_PATH/export.sh"
idf.py set-target esp32s3  # C3 则换成 esp32c3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
python3 tools/package_firmware.py build dist/esp32s3-n16r8
```

默认分区为两个 1.875 MiB OTA 槽，两种 Flash 都可容纳；S3 其余 Flash 暂未分配，避免不必要的数据分区。需要改输出脚可在 `idf.py menuconfig → ESP Reset` 分别配置三路，代码会拒绝重复 GPIO、恢复键、默认板型的 USB、内存、启动绑带及保留串口引脚（C3 额外禁止 20/21）；仍需核对具体公版板的原理图。

```bash
python3 -m unittest discover -s tests -v
```

CI 对两个芯片分别做真实 ESP-IDF 构建，并测试实际 C 模块的启动电平、脉冲、三路隔离、每路冷却和重复请求、维护互斥和 NVS 凭据处理。**尚未连接用户实物板验证 Wi-Fi、OTA 或目标 RESET 电气效果。**
