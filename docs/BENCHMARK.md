# AutoSim Studio – Performance and limits

How much AutoSim Studio can send, receive, decode and log, measured with `tools/benchmark.py`, and where the real
limits are in practice: the link to the adapter and the CAN bus, not the app.

```
uv run python tools/benchmark.py            # about 5 minutes
uv run python tools/benchmark.py --quick    # shorter runs
```

The script needs no hardware. It drives the real engine classes (TxScheduler, BusManager, RxDispatcher,
AutoResponder, the trace models, the export writers and the link protocols) with a generated database:
- 500 classic messages, 8 bytes, 8 signals of 8 bits each;
- 100 CAN FD messages, 64 bytes, 32 signals of 16 bits each.

## Summary

Measured on Linux, Python 3.11, 4 logical CPUs (a cloud VM, roughly a mid-range laptop). A desktop PC is usually
faster; Windows has coarser timers (AutoSim sets the 1 ms timer resolution while sending).

| Question | Answer |
|---|---|
| **Shortest cycle time** | **1 ms** (cycle times are whole milliseconds). One message at 1 ms: mean period 1.000 ms, 99 % of periods within 1.06 ms, worst case 1.8–3 ms. |
| **Most messages sent on time by the app** | about **30 000 frames/s** with a generator on every signal (e.g. 30 messages at 1 ms, or 300 at 10 ms, or 500 at 20 ms); about **60 000 frames/s** with fixed signal values |
| **Most CAN signals per second** | about **240 000 signal values/s** generated and encoded on time (8-signal messages, every signal driven by a generator); up to **310 000/s** at saturation; about **480 000/s** with fixed values |
| **What the bus and link allow** | far less: a 500 kbit/s bus carries about **4 100** 8-byte frames/s at 100 % load; 500k/2M CAN FD about **2 800** 64-byte frames/s; an ESP32 on USB-UART 921600 baud about **3 000** classic or **1 000** FD frames/s. The app is not the bottleneck. |
| **Receive / decode** | about **110 000–140 000 frames/s** (0.9–1.1 million signal values/s) annotated and decoded |
| **How many messages are logged** | Chronological trace: the last **200 000 frames** (ring buffer, the oldest are dropped; about 50 MB). Fixed trace: one row per message, **no time limit**. Signal plot: the last **20 000 samples per signal**. Queue between the adapters and the decoder: 200 000 frames. |
| **Export** | 200 000 frames in **0.4 s (CSV)**, **0.5 s (BLF)**, **0.7–0.9 s (ASC)** |
| **Auto response latency** | **0.12 ms median**, 0.3–0.5 ms p99, inside the app (delay 0 ms). Add the link: a few ms over USB / adb. |
| **Auto response rate** | up to **1 000 responses/s per rule**; above that the rule is switched off (loop guard) |

## 1. TX: cyclic sending

All cyclic messages are sent by one scheduler thread. For each frame it:
1. computes the generator values (sine, ramp, …);
2. encodes the signals into the payload;
3. hands the frame to the channel.

That takes about 25–35 µs per 8-signal frame with generators, and about 15 µs with fixed values.

### Timing of a single message

| Cycle | Mean period | 99 % of periods within | Worst seen |
|---|---|---|---|
| 1 ms | 1.000 ms | 1.06 ms | 1.8–3.0 ms |
| 5 ms | 5.000 ms | 5.07 ms | 5.2–5.7 ms |
| 10 ms | 10.000 ms | 10.08 ms | 10.1–10.9 ms |

There is no drift: the next send time advances by exactly one cycle from the previous *planned* time. The thread sleeps
until about 1.5 ms before a deadline, then waits actively. Occasional late frames (the "worst seen" column) come from
the operating system or from the GUI holding Python's interpreter lock.

### Many messages

8-signal messages, a sine generator on every signal:

| Load | Sent | On time | Period p99 |
|---|---|---|---|
| 10 × 1 ms | 9 950 frames/s | 99.5 % | 1.14 ms |
| 20 × 1 ms | 19 960 frames/s | 99.8 % | 1.31 ms |
| 30 × 1 ms | 29 940 frames/s | 99.8 % | 1.48 ms |
| 40 × 1 ms | 36 000 frames/s | 90 % | 2.1 ms |
| 50 × 1 ms | 39 500–41 500 frames/s | 80 % | 2.5–3.5 ms |
| 300 × 10 ms | 28 800 frames/s | 96 % | 19 ms (see below) |
| 500 × 20 ms | 24 980 frames/s | 99.9 % | 23 ms (see below) |

With fixed signal values (no generators): 60 × 1 ms = 59 200 frames/s (98.6 %); saturation about 60 000–69 000
frames/s.

CAN FD, 64 bytes, 32 signals with generators: saturation about 9 000–13 000 frames/s = 280 000–410 000 signal
values/s.

**Above the limit** the scheduler does not burst: messages simply come later, and every message still gets its turn.

**Many messages with the same cycle** are started at the same instant and fire together. 300 messages at 10 ms are a
burst of 300 frames (about 10 ms of work) every 10 ms, so the last ones in each burst are late by up to one cycle,
even though the average rate is right. The bus would serialise such a burst anyway (300 frames take 73 ms on a
500 kbit/s bus).

### Rule of thumb

Keep the total below about **30 000 frames/s with generators** (60 000 with fixed values). This is 7× more than a
fully loaded 500 kbit/s bus, so in practice the bus or the adapter is always the limit.

## 2. Links and the CAN bus

### App-side cost of the link protocols

| Protocol | Frame | Encode | Decode | Bytes on the wire |
|---|---|---|---|---|
| ESP32 serial (COBS + CRC16) | classic 8 B | 42 000 /s | 40 000 /s | 30 |
| ESP32 serial | FD 64 B | 14 000 /s | 14 000 /s | 86 |
| slcan (CANable) | classic 8 B | 1 500 000 /s | 970 000 /s | 22 |
| slcan | FD 64 B | 540 000 /s | 850 000 /s | 134 |

### Link capacity

| Link | Classic 8 B | CAN FD 64 B |
|---|---|---|
| ESP32 over USB-UART 921600 baud | about 3 000 frames/s per direction | about 1 000 frames/s |
| ESP32-S3 / C3 native USB | limited by the CAN bus | limited by the CAN bus |
| CANable 2.0 (USB full speed, slcan) | limited by the CAN bus | limited by the CAN bus |
| Android bridge (TCP / adb) | thousands of frames/s; latency a few ms over USB | same |

When a link is slower than the bus, the ESP32's STATUS reports `dropped` frames. Prefer the ESP32-S3 / C3 native USB
port for high CAN FD rates.

### CAN bus capacity at 100 % load

Standard IDs, about 10 % bit stuffing.

| Bus | Frame | One frame | Frames/s |
|---|---|---|---|
| 500 kbit/s classic | 8 B | 244 µs | 4 100 |
| 1 Mbit/s classic | 8 B | 122 µs | 8 200 |
| 500k / 2M CAN FD | 64 B | 359 µs | 2 800 |
| 500k / 5M CAN FD | 64 B | 180 µs | 5 500 |
| 1M / 8M CAN FD | 64 B | 105 µs | 9 500 |

The status bar shows each channel's estimated bus load. Real networks normally stay below 50–70 %.

### Simulated nodes

`SIM1`, `SIM2` run both ends of the ESP32 protocol inside the app, so they cost about twice as much CPU as a real
adapter. They carry about 2 500–5 000 frames/s. They are meant for trying things out, not for load tests.

## 3. RX: receiving and decoding

The RX dispatcher annotates every frame (name, cycle time, payload changed?) and decodes it with the databases.

| | Frames/s | Signal values/s |
|---|---|---|
| Decode only | 125 000–139 000 | 1.0–1.1 million |
| With 5 signals in the Signal plot | 107 000–120 000 | 0.86–0.96 million |

The GUI is refreshed 25 times per second with everything that arrived in between. The display rate therefore does not
depend on the bus load. The trace models accept rows far faster than any bus delivers them: the chronological trace
over 4 million rows/s, the fixed trace over 1.5 million frames/s.

## 4. Logging: what is kept and for how long

| Store | Capacity | When full |
|---|---|---|
| Chronological Bus trace (Fixed unticked) | **200 000 frames**, about 50 MB (250 bytes per frame) | the oldest frames are dropped |
| Fixed Bus trace | one row per message × channel × direction, with its latest data, cycle time and count | no limit in time |
| Signal plot | **20 000 samples per signal** (200 s of a 10 ms signal) | the oldest samples are dropped |
| Queue adapters → decoder | 200 000 frames | new frames are dropped (only if the decoder falls behind for seconds) |

How long the trace holds depends on the bus load:

| Bus traffic | Kept in the 200 000-frame trace |
|---|---|
| 1 000 frames/s (a typical 500 kbit/s bus) | the last 3 min 20 s |
| 4 000 frames/s (a full 500 kbit/s bus) | the last 50 s |
| 100 frames/s | the last 33 min |

**Export** writes the frames in the trace (after filters) to a file:

| Format | 200 000 frames | File size |
|---|---|---|
| CSV | 0.4 s | 12.5 MB |
| Vector BLF | 0.5 s | 1.4 MB |
| Vector ASC | 0.7–0.9 s | 12.5 MB |

To record longer than the trace holds, export regularly (Pause, Export, Clear) or filter the trace to the messages
you need.

## 5. Auto response

| | Value |
|---|---|
| Latency in the app (rule delay 0 ms), median / p99 | 0.11–0.12 ms / 0.3–0.5 ms |
| Worst case seen | about 4 ms (OS scheduling) |
| Rule look-up | by CAN ID, independent of the number of rules |
| Delay precision | about 0.1 ms (same timer technique as cyclic TX) |
| Loop guard | a rule firing more than 1 000 times/s is switched off, with a Log line |

End to end, add the link. On the Android bridge the tests measure a request → response round trip under 20 ms
(median) on localhost; adb over USB adds a few ms.

## 6. Other limits

| Item | Limit |
|---|---|
| Channels | no fixed limit; one per adapter (ESP32, CANable, bridge) plus simulated nodes |
| Cycle time | 1 ms to 60 000 ms, whole milliseconds |
| CAN FD payload | 0–64 bytes (non-standard lengths are padded to the next valid DLC) |
| ESP32 data rate | an integer multiple (1–10×) of the nominal rate, up to 8 Mbit/s (40 MHz crystal) |
| CANable data rate | 2 or 5 Mbit/s |
| Auto response rules | no fixed limit; up to 1 000 responses/s per rule |
| Bridge clients | 4 TCP clients per autosim-bridge |
| Connection watchdogs | ESP32: PING every 1 s, link dead after 3.5 s; bridge: reconnects every 1 s after 5 s of silence |

## 7. Tips for high loads

- Watch the bus load in the status bar first. The bus is almost always the limit.
- Use generators only where a signal must change. Fixed values cost half as much.
- For 1 ms messages, keep the total under about 30 000 frames/s with generators.
- Use the fixed trace for long sessions. Use the chronological trace (200 000 frames) to look at a short window in
  detail, and export it if you need to keep it.
- Remove signals from the Signal plot that you no longer watch.
- For high-rate CAN FD on an ESP32, use the native USB port (S3 / C3), not USB-UART.
