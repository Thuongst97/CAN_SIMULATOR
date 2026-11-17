# AutoSim CAN FD node: bản Arduino IDE (ESP32-S3 + MCP2518FD)

Sketch này là **cùng firmware** với bản PlatformIO trong `firmware/esp32_can_node`, chỉ đóng gói lại để mở và nạp
bằng Arduino IDE. Đấu dây xem [docs/ESP32_MCP2518FD_WIRING.md](../../../../docs/ESP32_MCP2518FD_WIRING.md).

```
AutoSimCanNode/
  AutoSimCanNode.ino     chương trình chính (sinh tự động, không sửa)
  autosim_config.h       CẤU HÌNH CỦA ANH: chân SPI, thạch anh, LED, baud ... (sửa ở đây)
  src/                   protocol, driver MCP2518FD, task FreeRTOS (sinh tự động, không sửa)
```

## 1. Cài đặt (một lần)

1. **Arduino IDE 2.x**.
2. **Board package ESP32**: *File → Preferences → Additional boards manager URLs*, thêm
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`. Sau đó vào *Tools → Board → Boards Manager*,
   tìm **esp32 by Espressif Systems** và cài **bản 3.x** (đã kiểm với core 3.3).
3. **Thư viện**: *Sketch → Include Library → Manage Libraries*, tìm **ACAN2517FD** (tác giả Pierre Molinaro) và cài
   bản 2.1.16 trở lên.

## 2. Đấu nối ESP32-S3-DevKitC-1 ↔ MCP2518FD (mặc định)

| MCP2518FD | ESP32-S3 |
|---|---|
| `SCK` | GPIO 12 |
| `SI` (MOSI) | GPIO 11 |
| `SO` (MISO) | GPIO 13 |
| `CS` | GPIO 10 |
| `INT` | GPIO 4 |
| `VCC` | **3V3** (logic 3,3 V; ESP32-S3 không chịu được 5 V) |
| `GND` | GND |

- Muốn dùng chân khác thì sửa `autosim_config.h` (ví dụ `#define CS_PIN 9`).
- Module dùng thạch anh 20 MHz thì thêm `#define OSC_FREQ 20`.
- LED RGB trên board: GPIO 48. Board DevKitC-1 **v1.1** dùng GPIO 38, khi đó thêm `#define LED_PIN 38`. Muốn tắt
  LED thì đặt `#define LED_PIN -1`.

## 3. Chọn cổng USB và cấu hình Tools

ESP32-S3-DevKitC-1 có **hai cổng USB-C**. Firmware chỉ nói chuyện với AutoSim qua **một** cổng, do menu
**USB CDC On Boot** quyết định. **AutoSim phải mở đúng cổng đó.**

| Menu *Tools* | Cách A: cổng **"UART"** (khuyên dùng) | Cách B: cổng **"USB"** (native) |
|---|---|---|
| Board | **ESP32S3 Dev Module** | **ESP32S3 Dev Module** |
| USB CDC On Boot | **Disabled** | **Enabled** |
| USB Mode | (mặc định) | **Hardware CDC and JTAG** |
| Upload Mode | UART0 / Hardware CDC | UART0 / Hardware CDC |
| Flash Size | theo module (4 MB đủ dùng) | như bên trái |
| PSRAM | Disabled (không cần) | như bên trái |
| Cổng COM chọn trong AutoSim | COM của chip USB-UART (CH343 / CP210x), **921600 baud** | COM của cổng native (VID:PID `303A:1001`) |
| Ưu / nhược | ổn định, reset khi nạp tự động | nhanh hơn (không giới hạn 921600 baud), cổng COM có thể đổi số sau khi reset |

Firmware tự chọn link theo menu này, không cần sửa code. "USB Mode: USB-OTG (TinyUSB)" cũng build được, nhưng
bản đã kiểm là *Hardware CDC and JTAG*.

## 4. Nạp

1. Mở `AutoSimCanNode/AutoSimCanNode.ino` bằng Arduino IDE.
2. Chọn board, cổng và các menu như bảng trên.
3. Bấm **Upload**. Nếu không vào được chế độ nạp: giữ **BOOT**, nhấn **RESET**, thả BOOT rồi Upload lại. Nạp xong
   nhấn **RESET** một lần.
4. **Đóng Serial Monitor** của Arduino IDE trước khi Connect trong AutoSim, vì một cổng COM chỉ mở được ở một chỗ.
   Firmware dùng protocol nhị phân, nên Serial Monitor chỉ hiện ký tự rác; đó là bình thường.

## 5. Kết nối với AutoSim

1. *Channels → Scan*, chọn cổng COM ở bảng mục 3.
2. Chọn bit rate (ví dụ 500 kbit/s / 2 Mbit/s, FD, BRS). Tick **Loop** cho lần thử đầu tiên, khi chưa nối bus.
3. **Connect**. Bảng kênh hiện `ESP32-xxxxxx` và trạng thái `running`.

Nếu báo `HELLO: no answer`:
- sai cổng, ví dụ nạp bản *USB CDC On Boot = Enabled* nhưng lại mở cổng "UART";
- chưa nhấn RESET sau khi nạp;
- Serial Monitor còn đang mở cổng.

Kiểm tra nhanh: `uv run python firmware/esp32_can_node/tools/probe_port.py COMx`. Các lỗi khác xem mục 7 của
tài liệu đấu nối.

## 6. Cập nhật sketch khi firmware thay đổi (dành cho người phát triển)

Source gốc là bản PlatformIO. Sau khi sửa `firmware/esp32_can_node/src` hoặc `lib/autosim_proto`, chạy:
```
python firmware/esp32_can_node/tools/make_arduino_sketch.py
```
`autosim_config.h` không bị ghi đè. Test `tests/test_arduino_sketch.py` sẽ báo lỗi nếu sketch không khớp source.
