# Checklist môi trường Android (CAN simulation)

Tài liệu này dùng để khảo sát một head unit hoặc Android VM trước khi simulate CAN. Mục tiêu là trả lời được bốn câu:
- VHAL đi xuống CAN bằng đường nào: `AF_CAN`, SPI/UART tới MCU, hay vsock/IPC tới host?
- `can0` do driver nào tạo ra và nằm trên bus gì?
- VHAL là loại gì (AIDL hay HIDL) và chạy ở process nào?
- Bridge của AutoSim có chạy được không: có vcan không, có root không, SELinux có chặn không?

Bối cảnh các layer và điểm simulate: [ANDROID_CAN_LAYERS.md](ANDROID_CAN_LAYERS.md). Cách triển khai bridge:
[ANDROID_BRIDGE.md](ANDROID_BRIDGE.md).

---

## 0. Chạy tự động

```
scripts\android_bridge.cmd check                       (Windows)
bridge/autosim-bridge/android_bridge.sh check          (Linux / macOS)
```

Lệnh này copy `bridge/autosim-bridge/android_env_check.sh` lên máy, chạy nó và lưu report thành
`android_env_report_<thời gian>.txt` trên PC. Report chia thành 10 mục, đánh số giống các mục dưới đây.

Chạy tay:
```
adb root
adb push bridge/autosim-bridge/android_env_check.sh /data/local/tmp/
adb shell sh /data/local/tmp/android_env_check.sh can0
```

- Script chỉ đọc. Riêng mục 10 có tạo rồi xoá ngay một vcan tạm (`autosim_chk`) khi chạy bằng root.
- Nếu không có root (bản user) thì vẫn chạy được, nhưng các mục 4, 7, 8 sẽ thiếu thông tin.
- Interface cần kiểm tra là tham số thứ nhất (mặc định `can0`).
- Nếu script không tìm được process VHAL theo tên thì chỉ định pid tay: `VHAL_PID=1234 sh …`.

Phần dưới giải thích từng mục: lệnh nào, nhìn vào đâu, và kết quả nghĩa là gì.

---

## 1. Build

| Kiểm tra | Lệnh | Cần thấy |
|---|---|---|
| Phiên bản Android | `getprop ro.build.version.release` / `ro.build.version.sdk` | SDK ≥ 33 (Android 13) thường dùng VHAL AIDL; thấp hơn thường là HIDL |
| Loại build | `getprop ro.build.type`, `ro.debuggable` | `userdebug` hoặc `eng`, `debuggable=1`; nếu không thì không `adb root` được và không deploy bridge kiểu bench được |
| ABI | `getprop ro.product.cpu.abi` | `arm64-v8a` hoặc `x86_64`, để chọn binary NDK |
| SELinux | `getenforce` | `Enforcing` là bình thường; nếu bridge bị chặn thì xem `adb logcat \| grep avc` |
| Có phải AAOS không | `pm list features \| grep automotive` | `android.hardware.type.automotive` |

## 2. Kernel có hỗ trợ CAN không

| Kiểm tra | Lệnh | Cần thấy / ý nghĩa |
|---|---|---|
| Config kernel | `zcat /proc/config.gz \| grep CONFIG_CAN` | `CONFIG_CAN`, `CONFIG_CAN_RAW`; `CONFIG_CAN_VCAN=y` hoặc `=m` để có vcan |
| Protocol đã đăng ký | `grep -i can /proc/net/protocols` | có `CAN_RAW` nghĩa là `AF_CAN` raw socket dùng được |
| SocketCAN đang chạy | `ls /proc/net/can` | thư mục tồn tại nghĩa là module `can` đã load |
| Module | `lsmod \| grep -i can`, `ls /vendor/lib/modules /vendor_dlkm/lib/modules \| grep can` | `vcan.ko`, `can-raw.ko`, driver controller (`m_can`, `flexcan`, `mcp251xfd`…) |

Nếu không có `/proc/config.gz` (kernel tắt `CONFIG_IKCONFIG_PROC`) thì dựa vào `/proc/net/protocols` và danh sách
module.

## 3. Interface CAN

| Kiểm tra | Lệnh | Cần thấy / ý nghĩa |
|---|---|---|
| Interface CAN thật | `ip -d link show type can` | ví dụ `can0`, và dòng `can <FD> state ERROR-ACTIVE … bitrate 500000 … dbitrate 2000000` |
| vcan có sẵn | `ip -d link show type vcan` | thường rỗng; sau `takeover` thì `can0` hiện ở đây |
| Chi tiết + thống kê | `ip -d -s link show can0` | MTU **72** là CAN FD, **16** là classic CAN; bộ đếm RX/TX/error cho biết có traffic |
| Không có `ip` | `cat /sys/class/net/*/type`, `cat /sys/class/net/can0/mtu` | type **280** là CAN |

## 4. Driver nào, nằm trên bus gì

| Kiểm tra | Lệnh | Ý nghĩa |
|---|---|---|
| Driver | `basename $(readlink -f /sys/class/net/can0/device/driver)` | `m_can_platform`, `flexcan`, `rcar_canfd` … là controller nằm trong SoC (MMIO); `mcp251xfd` là chip CAN nối qua **SPI**; `virtio_can` là CAN do **hypervisor** cung cấp |
| Bus | `basename $(readlink -f /sys/class/net/can0/device/subsystem)` | `platform`, `spi`, `virtio`, `pci` hoặc `usb` |
| Đường dẫn thiết bị | `readlink -f /sys/class/net/can0/device` | ví dụ `…/spi0/spi0.0` hoặc `…/virtio5` |
| Device tree | `cat /sys/class/net/can0/device/of_node/compatible` | ví dụ `bosch,m_can`, `microchip,mcp2518fd` |
| Không có link `device` | — | interface phần mềm (vcan, vxcan, slcan) |
| Log kernel | `dmesg \| grep -i -E "can|m_can|flexcan|mcp251|virtio_can"` | lỗi probe, bitrate, bus-off |

## 5. Ảo hoá / hypervisor

| Kiểm tra | Lệnh | Ý nghĩa |
|---|---|---|
| Thiết bị virtio | `cat /sys/bus/virtio/devices/*/device` | `0x0024` là virtio-can; `0x0013` là vsock (kênh thường dùng để VHAL nói với host); `0x0001` là mạng |
| Hypervisor | `cat /proc/device-tree/hypervisor/compatible`, `cat /sys/hypervisor/type`, `dmesg \| grep -i hypervisor` | Gunyah, Xen, KVM, QNX… |
| vsock | `ls -l /dev/vsock` | có vsock nghĩa là VHAL có thể đang nói chuyện với host VM bằng gRPC hoặc IPC (xem mục 7) |

## 6. Vehicle HAL

| Kiểm tra | Lệnh | Ý nghĩa |
|---|---|---|
| VHAL AIDL | `service list \| grep automotive.vehicle` | `android.hardware.automotive.vehicle.IVehicle/default` → stable AIDL (Android 13+), Binder |
| VHAL HIDL | `lshal \| grep automotive.vehicle` | `android.hardware.automotive.vehicle@2.0::IVehicle/default` → HIDL, hwbinder |
| CAN bus HAL | `service list \| grep automotive.can`, `lshal \| grep automotive.can` | có thì VHAL có thể đi qua CAN HAL (`ICanBus`) thay vì tự mở socket |
| Process VHAL | `ps -A \| grep -i vehicle` | tên process, ví dụ `android.hardware.automotive.vehicle@V3-<vendor>-service` |
| Binary + domain | `readlink /proc/<pid>/exe`, `cat /proc/<pid>/attr/current` | dùng để đọc sepolicy của vendor |
| File init | `grep -l vehicle /vendor/etc/init/*.rc` | xem tham số và tên interface trong lệnh khởi động |

## 7. VHAL nói chuyện với cái gì

Đây là mục quan trọng nhất. Mở danh sách file descriptor của VHAL:

```
adb shell ls -l /proc/<pid VHAL>/fd
```

- **socket**: mỗi dòng `socket:[inode]` là một socket. Tra inode trong `/proc/net/{tcp,tcp6,udp,unix,netlink,raw,packet}`.
  Socket **không có** trong các bảng đó là `AF_CAN` hoặc `AF_VSOCK`. Script làm việc tra này tự động.
- **device**: `/dev/spidev*` hoặc `/dev/tty*` nghĩa là VHAL nói thẳng với MCU; `/dev/vsock` hoặc `/dev/rpmsg*` nghĩa
  là VHAL nói với host hoặc coprocessor.
- Chỉ có `unix` và binder: VHAL nhờ một daemon khác làm việc CAN. Tìm daemon đó ở mục 9.

## 8. Ai đang nghe trên interface CAN

```
adb shell cat /proc/net/can/rcvlist_all
```

Bảng này liệt kê các filter nhận của từng CAN socket, kèm cột **device** là interface mà socket đó bind vào.
- Có dòng với device `can0` nghĩa là chắc chắn có process đang mở `AF_CAN` trên `can0`.
- Kết hợp với mục 7: VHAL có socket loại "OTHER" và `rcvlist_all` có `can0` thì VHAL dùng `AF_CAN`.
- `cat /proc/net/can/stats` cho biết số frame đã nhận và gửi.

## 9. SPI / UART / MCU

| Kiểm tra | Lệnh | Ý nghĩa |
|---|---|---|
| spidev | `ls -l /dev/spidev*` | userspace nói SPI trực tiếp (protocol MCU) |
| Thiết bị SPI và driver | `ls /sys/bus/spi/devices`, xem `modalias`, `driver` | `mcp251xfd` là chip CAN (xem mục 4); `spidev` là kênh thô tới MCU |
| UART / rpmsg | `ls /dev/ttyHS* /dev/ttyS* /dev/ttyMSM* /dev/rpmsg*` | MCU hoặc coprocessor nói qua serial hoặc rpmsg |
| Daemon MCU | `ps -A \| grep -i -E "mcu\|ioc\|vip\|vehicle\|can"` | daemon riêng của vendor đứng giữa VHAL và MCU |

## 10. Điều kiện cho AutoSim bridge

| Kiểm tra | Lệnh | Cần thấy |
|---|---|---|
| Tạo được vcan | `ip link add dev t type vcan && ip link set t mtu 72 && ip link del t` (root) | thành công; nếu không thì phải load hoặc build `vcan.ko` |
| Cổng TCP | `grep -i ":7360 " /proc/net/tcp` (7360 là 29536 ở dạng hex) | không có dòng nào, tức cổng còn trống |
| IP cho chế độ Ethernet | `ip -4 addr` | IP của head unit, dùng khi bridge chạy với `-b 0.0.0.0` |
| Bridge đang chạy | `pidof autosim-bridge` | — |

---

## Đọc kết quả: chọn cách simulate

| Kết quả khảo sát | Trường hợp | Cách simulate |
|---|---|---|
| VHAL có socket "OTHER", `rcvlist_all` có `can0`; driver `m_can` / `flexcan` / `mcp251xfd` / `virtio_can` | VHAL → `AF_CAN` → CAN driver (qua MMIO, SPI hoặc virtio) | **B · vcan + bridge**: `android_bridge check` rồi `deploy -RestartVhal` |
| Như trên nhưng **không tạo được vcan** | thiếu `CONFIG_CAN_VCAN` | build `vcan.ko` đúng kernel (GKI vendor module); tạm thời dùng **E · HIL** |
| VHAL mở `/dev/spidev*` hoặc `/dev/tty*`, hoặc có daemon MCU; không có `can0` | VHAL → protocol riêng → MCU giữ CAN | **E · HIL** trên bus; hoặc thay transport của daemon / làm "MCU giả" (cần spec protocol) |
| VHAL chỉ có vsock, có `/dev/vsock`, không có `can0` | VHAL → gRPC / IPC → host VM giữ CAN | **A** (GRPC VHAL) hoặc **C** (server IPC giả); hoặc **D** nếu sửa được host |
| VHAL chỉ có binder / unix, có CAN bus HAL | VHAL → CAN HAL (`ICanBus`) → `AF_CAN` | vẫn là **B**: khai báo bus của CAN HAL là vcan |
| Bản `user`, không root | — | xin bản `userdebug`; hoặc **E · HIL** |

## Mẫu ghi kết quả

| Mục | Giá trị |
|---|---|
| Thiết bị / build fingerprint | |
| Android / SDK / build type | |
| SELinux | |
| Hypervisor (nếu có) | |
| Interface CAN, MTU, bitrate / dbitrate | |
| Driver / bus | |
| VHAL: AIDL / HIDL, tên process | |
| VHAL nói chuyện qua | AF_CAN / spidev / tty / vsock / CAN HAL |
| Tạo được vcan (FD) | |
| Cách simulate chọn | B / A / C / D / E |
| Ghi chú | |
