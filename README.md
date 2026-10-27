# ESP Reset

把 ESP32 变成独立的网络复位按键：配网后打开设备自己的网页，即可短暂接通外部电子开关，模拟按下另一块开发板的 RESET。复位模块提取自 [Ken-u/esphook](https://github.com/Ken-u/esphook) 的复位功能，已去掉显示屏、LVGL、字库、蜂鸣器、Agent Hook 和主机 daemon 依赖。

## 支持的板子

| 板子 | 构建目标 | 默认复位输出 | 板载恢复键 | Flash |
| --- | --- | --- | --- | --- |
| ESP32-S3 公版 N16R8 / DevKitC 类板 | `esp32s3` | **GPIO4** | BOOT / GPIO0 | 16 MB |
| ESP32-C3 SuperMini | `esp32c3` | **GPIO20** | BOOT / GPIO9 | 4 MB |

GPIO 是芯片编号，以板上丝印为准。**S3 不要照旧 C3 图使用 GPIO20：S3 的 GPIO19/20 是原生 USB。** N16R8 的 8 MB PSRAM 无需使用，本项目不初始化 PSRAM；GPIO26–37 的内存引脚也不会用作复位输出。该功能只需片上 RAM。两种固件不可混刷。

## 功能

- 首次启动的 WPA2 配网热点；手机/电脑浏览器配网，无需安装 App。
- Wi-Fi 凭据和独立随机管理密码持久化；联网稳定 30 秒后关闭热点，失联约 60 秒恢复热点。
- 设备网页：网络/固件/复位状态、目标板复位、Wi-Fi 设置、应用 OTA、控制器重启。
- 一次约 200 ms 的高电平脉冲，松开后冷却 3 秒；没有保持按下或自动反复复位功能。
- 管理密码鉴权；API 和 CLI；同一启动内最近 8 个成功 request_id 去重。
- USB 完整刷写包、独立 OTA 应用镜像；CI 构建 C3 和 S3 两个版本。

## 硬件先接对

**GPIO 不是干接点，不能直接跨接未知按键。** 用 GPIO4（S3）或 GPIO20（C3）驱动外部高电平有效的 MOSFET / PhotoMOS 开关。

接地触发的 RESET 按键可以用 2N7002 + 330 Ω + 4.7 kΩ。接线与实体脚位见 [docs/hardware.md](docs/hardware.md) 和 [接线图](docs/reset-wiring.svg)。如果希望通用地模拟两根未知按键线，选双向输出、3.3 V 高电平触发的隔离 PhotoMOS 模块。

先不要接目标板，验证 ESP 输出空闲低、操作时短暂拉高，再接入目标 RESET。外部下拉必须保留，确保上电、刷机及 ESP 自身重启期间开关保持断开。

## 从构建产物刷机

在仓库 **Actions → 最新成功的 Build and test → Artifacts** 下载对应的：

- `esp-reset-esp32s3-n16r8`
- `esp-reset-esp32c3-supermini`

解压后安装 esptool，并刷入所有分区文件：

```bash
python3 -m pip install esptool pyserial
python3 flash.py --port /dev/ttyACM0
# macOS 替换成 /dev/cu.usbmodem... 或 USB 转串口实际设备路径
```

`flash.py` 自动读取包内 `flasher_args.json`，使用匹配芯片、Flash 大小和地址，不需要手工猜烧录偏移。Windows 使用 `python flash.py --port COM5`。如果无法自动进下载模式，按住 BOOT、按一下板子的 RESET/EN、松开 BOOT 后再刷。NVS 不会被普通刷写或 OTA 清除。

## 首次配网

1. 刷机后用 USB 串口监视器打开 115200 波特率，按一下 ESP 板的 RESET/EN，找到启动日志：

   ```text
   Management + recovery AP password: <24 位随机字符串>
   Recovery AP: ESP-Reset-XXXXXX
   ```

   也可运行 `python3 -m serial.tools.miniterm /dev/ttyACM0 115200`。日志同时输出到 UART0 和原生 USB Serial/JTAG；S3 板上若有 USB-UART 与 USB/OTG 两个口，两者均可查看启动日志，选择对应串口即可。原生 USB 日志为非阻塞输出，先打开监视器再按 RESET/EN。

2. 手机/电脑连接 `ESP-Reset-XXXXXX`，密码使用上述随机字符串；即使系统提示无互联网，也保持连接。
3. 手动打开 **http://192.168.4.1/**（不依赖自动弹出配网页）。同一字符串也是管理密码。
4. 输入家里/办公室的 **2.4 GHz** Wi-Fi 名称和密码，点击保存并连接。
5. 页面显示联网后的设备 IP；后续直接打开该 IP。也可在路由器 DHCP 列表中找 `esp-reset-xxxxxx`。

首次没有可用串口监视器时不要猜默认密码；本项目不使用固定出厂密码。管理密码可随时从可信 USB 启动日志重新读取。Wi-Fi 密码不会经网页状态接口返回。

## 正常使用与恢复

网页“重置目标开发板”会先确认，再执行 200 ms 左右脉冲。成功只表示外部开关已松开，不表示目标系统已经启动完成。

控制器运行时长按 **BOOT 约 5 秒**，仅清除 Wi-Fi 并恢复热点，管理密码保持不变。上电就按住 BOOT 会进入 ROM 下载模式；这里指正常运行后再长按。

网页“重启 ESP 控制器”重启的是控制器；目标板复位是另一个按钮。网页 OTA 上传对应芯片的 **`esp-reset.bin`**，不要上传 bootloader、分区表或其他项目的固件。OTA 校验芯片、映像和项目名称；校验失败不切换启动分区。新应用成功初始化网络及网页后才取消回滚。凭据存于 NVS，升级保留。

所有控制在本地 HTTP 上完成，适用于可信局域网。跨网段访问可使用路由/VPN；此独立版本没有原 esphook 的反向主机连接，不能依靠它穿过单向 NAT。不要直接把设备 HTTP 端口暴露到公网。

## CLI 与 API

不需要常驻主机进程，CLI 仅在执行时请求设备：

```bash
python3 tools/esp_reset.py --url http://192.168.1.50 status
python3 tools/esp_reset.py --url http://192.168.1.50 reset
python3 tools/esp_reset.py --url http://192.168.1.50 wifi 'MyWiFi'
python3 tools/esp_reset.py --url http://192.168.1.50 ota esp-reset.bin
```

默认交互输入管理密码；自动化可通过 `ESP_RESET_TOKEN` 环境变量提供。CLI 不自动重试任何复位请求。超时可能发生在脉冲执行后，先观察目标板状态再决定是否再次操作。

| 接口 | 方法 | 内容 |
| --- | --- | --- |
| `/` | GET | 设备网页 |
| `/api/status` | GET | 状态，不含密码 |
| `/api/reset` | POST JSON | `{"request_id":"一次操作的唯一ID"}` |
| `/api/wifi` | POST JSON | `{"ssid":"WiFi名","password":"密码"}` |
| `/api/reboot` | POST JSON | `{}`，重启 ESP |
| `/api/ota` | POST binary | 同芯片 `esp-reset.bin` |

除 `/` 外均需请求头 `X-Reset-Token: <管理密码>`。JSON 请求需 `Content-Type: application/json`。复位冷却/维护中返回 409；松开后才返回 `{"ok":true,"state":"released"}`。request_id 是同次操作的幂等键，去重只覆盖本次启动中最近 8 个成功请求，不跨重启、不无限缓存。

## 从源码构建

使用 **ESP-IDF v5.5.4**：

```bash
source "$IDF_PATH/export.sh"
idf.py set-target esp32s3  # C3 则换成 esp32c3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
python3 tools/package_firmware.py build dist/esp32s3-n16r8
```

默认分区为两个 1.875 MiB OTA 槽，两种 Flash 都可容纳；S3 其余 Flash 暂未分配，避免不必要的数据分区。需要改输出脚可在 `idf.py menuconfig → ESP Reset` 配置，代码会拒绝默认板型的 USB、内存、启动绑带及保留串口引脚；仍需核对具体公版板的原理图。

```bash
python3 -m unittest discover -s tests -v
```

CI 对两个芯片分别做真实 ESP-IDF 构建，并测试实际 C 模块的启动电平、脉冲、冷却、重复请求、维护互斥和 NVS 凭据处理。**尚未连接用户实物板验证 Wi-Fi、OTA 或目标 RESET 电气效果。**
