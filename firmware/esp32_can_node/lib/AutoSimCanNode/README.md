# AutoSimCanNode: thư viện Arduino (ESP32-S3 + MCP2518FD)

Thư viện biến ESP32-S3 (hoặc ESP32 classic, ESP32-C3) gắn module MCP2518FD thành **một kênh CAN / CAN FD của
AutoSim Studio**. Chỉ cần cài thư viện một lần; sketch chính chỉ có vài dòng, và cấu hình chân ngay trong `setup()`.

```cpp
#include <AutoSimCanNode.h>

void setup() {
  autosim::BoardConfig cfg = autosim::defaultBoardConfig();   // mặc định theo chip đang build
  // cfg.cs = 9;  cfg.ledPin = 38;                             // chỉ sửa chỗ khác với board của anh
  autosim::startCanNode(cfg);
}

void loop() { vTaskDelay(portMAX_DELAY); }                    // node chạy trong task FreeRTOS riêng
```

## 1. Cài đặt (một lần)

1. **Board package**: *File → Preferences → Additional boards manager URLs*, thêm
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`. Sau đó *Tools → Board → Boards Manager*,
   cài **esp32 by Espressif Systems** bản **3.x**.
2. **Thư viện AutoSimCanNode**: *Sketch → Include Library → **Add .ZIP Library…***, chọn file
   `AutoSimCanNode-<version>.zip`.
3. **Không cần cài thêm gì.** Driver MCP2518FD (ACAN2517FD 2.1.16 của Pierre Molinaro, giấy phép MIT, xem
   `LICENSE-ACAN2517FD.txt`) đã nằm sẵn trong thư viện. Nếu máy có cài ACAN2517FD riêng thì cũng không sao: sketch
   luôn dùng bản đi kèm.

File ZIP có sẵn trong repo: `firmware/esp32_can_node/release/AutoSimCanNode-<version>.zip`. Trên GitHub, mở file đó
rồi bấm **Download raw file**. Khi có phiên bản mới, chỉ cần *Add .ZIP Library* lại với file mới. Người phát triển
tạo lại ZIP bằng `python firmware/esp32_can_node/tools/make_arduino_zip.py`.

## 2. Mở ví dụ và nạp

1. *File → Examples → AutoSimCanNode → **AutoSimCanNode***, rồi *File → Save As* để lưu một bản sketch của anh.
2. *Tools*:

| Menu | Cổng **"UART"** của S3 (khuyên dùng) | Cổng **"USB"** native |
|---|---|---|
| Board | ESP32S3 Dev Module | ESP32S3 Dev Module |
| USB CDC On Boot | **Disabled** | **Enabled** |
| USB Mode | mặc định | Hardware CDC and JTAG |
| Cổng chọn trong AutoSim | COM của chip USB-UART, 921600 baud | COM `303A:1001` |

3. **Upload**. Nếu không vào được chế độ nạp: giữ BOOT, nhấn RESET, thả BOOT rồi upload lại. Nạp xong nhấn RESET.
4. **Đóng Serial Monitor** trước khi Connect trong AutoSim. Không dùng `Serial.print` trong sketch, vì Serial là
   đường protocol nhị phân nói chuyện với AutoSim.

## 3. Đấu nối ESP32-S3 ↔ MCP2518FD (mặc định)

| MCP2518FD | ESP32-S3 | `BoardConfig` |
|---|---|---|
| SCK | GPIO 12 | `cfg.sck` |
| SI (MOSI) | GPIO 11 | `cfg.mosi` |
| SO (MISO) | GPIO 13 | `cfg.miso` |
| CS | GPIO 10 | `cfg.cs` |
| INT | GPIO 4 | `cfg.intPin` |
| VCC | **3V3** (ESP32 không chịu được 5 V) | |
| GND | GND | |

Các tham số khác:
- `cfg.oscMHz`: thạch anh của module. Mặc định `0` = **tự đo** mỗi lần Connect (từ 1.4), nên module 20 MHz (vỏ thạch
  anh in `H20.00`) hay 40 MHz đều chạy đúng bit rate. Chỉ đặt `20` / `40` khi muốn cố định. Với 20 MHz nên dùng FD tới
  500k/2M; 5M chập chờn, 8M không đạt được.
- `cfg.ledPin`: LED báo hoạt động. LED RGB: **xanh dương** = nhận được frame từ bus, **xanh lá** = đã gửi frame AutoSim
  yêu cầu, **đỏ** = không gửi được (bus-off, không có ACK...). RGB GPIO 48; board DevKitC-1 v1.1 dùng 38; `-1` là tắt.
- `cfg.uartBaud`: chỉ dùng với cổng UART, phải bằng biến môi trường `AUTOSIM_BAUD` của app (mặc định 921600).
- `cfg.xstby` (mặc định `true`): MCP2518FD dùng chân INT0/GPIO0 kéo STBY của transceiver xuống thấp để transceiver
  hoạt động. Nhiều module nối STBY vào chân này; nếu không bật, transceiver nằm ở standby và báo lỗi `nominal BIT0` /
  `no ACK`. Chỉ tắt khi anh dùng INT0 vào việc khác.
- `cfg.nodeName`: tên node hiện trong AutoSim; nếu bỏ trống là `ESP32-<MAC>`.

Hướng dẫn đấu nối đầy đủ (transceiver, 120 Ω, nối chung GND, TDC, ý nghĩa các lỗi bus):
`docs/ESP32_MCP2518FD_WIRING.md` trong repo.

Nếu cấu hình sai (hai tín hiệu dùng chung một chân, chân không tồn tại, thạch anh không phải 20/40), board vẫn chạy
và lỗi hiện trong **Log của AutoSim** khi Connect, ví dụ `MCP2518FD fault - configuration: CS and INT both use
GPIO 4`. Lý do là cổng Serial không dùng để in debug được. LED đặt trùng chân SPI sẽ tự bị tắt.

## 4. Kết nối với AutoSim

1. *Channels → Scan*, chọn cổng COM ở bảng mục 2. Lần đầu nên tick **Loop** (thử không cần bus).
2. Chọn bit rate. Data rate phải là bội số nguyên 1–10 của nominal, ví dụ 500k/2M hoặc 500k/5M.
3. **Connect**. Bảng kênh hiện `ESP32-xxxxxx · running`.

Nếu báo `HELLO: no answer`: chọn sai cổng (kiểm lại menu USB CDC On Boot), chưa nhấn RESET sau khi nạp, hoặc
Serial Monitor còn đang mở cổng.

## 5. Dành cho người phát triển

Thư mục này là **source duy nhất** của firmware. PlatformIO (`firmware/esp32_can_node`, `pio run`) và Arduino IDE
dùng chung nó. Phần protocol và logic node (`autosim_protocol.*`, `autosim_node.*`, `rx_blink.h`) không phụ thuộc
phần cứng và có unit test trên PC (`pio test -e native`). Phần gắn với ESP32 (`AutoSimCanNode.cpp`,
`can_mcp2518fd.cpp`) chỉ được build khi target là ESP32.
