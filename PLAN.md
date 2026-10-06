# PROMPT REQ: BUILD AN AUTOMOTIVE CAN & SOME/IP SIMULATION SUITE (PYTHON + PYSIDE6)

Act as a Senior Automotive Software Engineer and Principal System Architect. Help me design and code a desktop application called "AutoSim Studio" - a CAN FD and SOME/IP Simulator in Python.

---

### 1. TECH STACK & REQUIREMENTS

- **Language**: Python 3.11+
- **GUI Framework**: PySide6 (Qt 6) with dark theme (QDarkStyle / Custom QSS), supporting tabbed & dockable widgets (PyQtDarkTheme or QtDocking).
- **Core Libraries**:
    - `cantools` & `python-can` for CAN/CAN-FD database handling and bus interface.
    - `someipy` for SOME/IP & SOME/IP-SD implementation.
    - `protobuf` & `google.protobuf` for SOA payload encoding/decoding.
    - `pyserial` / `pyqtgraph` for hardware interface & real-time trace/graph plotting.

---

### 2. ARCHITECTURE & SYSTEM DESIGN

Implement a clean, modular architecture (Model-View-Presenter or Service-Oriented Architecture):

1. `core/can_engine`: Handles DBC parsing, signal mapping, periodic TX threads, RX handling, and multi-channel hardware abstraction.
2. `core/someip_engine`: Handles SOME/IP service configuration, SOME/IP-SD (Service Discovery), offer/subscribe logic, methods, and Protobuf/SOA payload serialization.
3. `hardware/esp32_gateway`: Communication bridge for multiple ESP32 + MCP2518FD nodes over Serial/Socket (custom protocol or SLCAN FD extension).
4. `hardware/eth_interface`: Ethernet interface selector for Automotive Ethernet Media Converters (Intrepid RAD-MOON).
5. `ui/`: Modern PySide6 GUI panels.

---

### 3. DETAILED FEATURE REQUIREMENTS

#### Feature A: Hardware Connection Manager

- **Multi-ESP32 Gateway**:
    - Ability to scan and manage multiple COM ports (ESP32 + MCP2518FD nodes).
    - Configurable bitrate, data rate (e.g., Nominal 500kbps, Data 2Mbps/5Mbps), BRS (Bit Rate Switch), and ISO/non-ISO CAN-FD modes for each node.
    - Custom protocol packet handler over Serial to transmit raw CAN FD frames to ESP32.
- **Automotive Ethernet Manager (RAD-MOON)**:
    - Network Interface Card (NIC) drop-down selector (binding `someipy` to the specific Ethernet interface connected to RAD-MOON).
    - Network configuration parameters (Unicast IP, Multicast IP for SD, Port numbers).

#### Feature B: CAN Simulator Module (Vector-like functionality)

1. **DBC / ARXML Loader**:
    - Load `.dbc` or `.arxml` files.
    - Display nodes, messages, and signals in a tree view with signal details (bit position, length, factor, offset, min/max, unit).
2. **Signal Transmission & Simulation**:
    - Transmit CAN/CAN-FD messages periodically or on-event.
    - Interactive Signal Panel: Sliders, knobs, and input fields to manually change signal values in real-time.
    - Built-in Signal Generators: Sine wave, ramp, toggle, square wave generators for automated testing.
3. **Trace & Bus Monitor Window**:
    - Real-time logging table: Timestamp, Channel/Node, ID (Hex), Name, DLC, Data (Hex), Cycle Time.
    - Message filtering (by ID, Name, Node).
    - Export bus logs to `.csv` or `.blf` / `.asc` formats.

#### Feature C: SOME/IP Simulator Module

1. **Service Configuration & SOA Package Loader**:
    - Load vsomeip JSON configurations, ARXML, or Protobuf definition files (`.proto`).
    - Dynamic UI generator based on Protobuf messages / SOA field definitions.
2. **SOME/IP-SD (Service Discovery)**:
    - Configurable Client/Server roles.
    - Server mode: Offer Service, Eventgroups, and periodic Event notifications.
    - Client mode: Find Service, Subscribe Eventgroups, send Request-Response Methods.
3. **Payload Inspection & Triggering**:
    - Payload encoder/decoder supporting raw bytes, SOME/IP primitive types, and Protobuf structures.
    - Method caller UI (input parameters -> send request -> display response).
    - Event Publisher UI (modify field/event data -> publish to network via RAD-MOON).

---

### 4. DELIVERABLES REQUESTED

Please provide the implementation step-by-step:

1. **Project Directory Structure**: High-level folder layout and module design.
2. **Hardware & Protocol Specification**: Custom serial protocol specification between PC and ESP32 (MCP2518FD) for CAN-FD frames.
3. **Core Backend Implementation**:
    - CAN Engine (`cantools` integration + periodic TX thread).
    - SOME/IP Engine using `someipy` + Protobuf parser.
    - Serial Manager for ESP32 nodes.
4. **PySide6 UI Implementation**:
    - Main Window layout with Docking Panels (Connection Bar, CAN DBC Explorer, Signal Panel, SOME/IP Panel, Real-time Bus Trace).
    - Dark theme styling sheet (QSS).

Start by outlining the project structure, the communication protocol for ESP32, and the core CAN/SOME-IP engine scripts. Write modular, highly maintainable Python code with clear docstrings and error handling.
