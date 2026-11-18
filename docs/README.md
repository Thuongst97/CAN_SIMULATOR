# AutoSim Studio documentation

## Start here

| Document | For | Contents |
|---|---|---|
| [../README.md](../README.md) | Everyone | What AutoSim Studio is, features, quick start |
| [SETUP.md](SETUP.md) | Users, developers | Installer, development environment, ESP32 / CANable / Android bridge setup, troubleshooting |
| [USER_GUIDE.md](USER_GUIDE.md) | Users | The window, every desktop and window, workflows, shortcuts |

## Design

| Document | Contents |
|---|---|
| [design/SYSTEM_DESIGN.md](design/SYSTEM_DESIGN.md) | System context, goals and non-goals, requirements, solution overview, key decisions, risks |
| [design/HLD.md](design/HLD.md) | Layers, components, interfaces, data flows, threading model, persistence, UI organisation, error handling |
| [design/LLD.md](design/LLD.md) | Data types, engine algorithms, back-ends and wire protocols, UI internals, firmware and bridge, sequences, tests, extending |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deployment units and diagrams, build and packaging, configuration, upgrades |
| [BENCHMARK.md](BENCHMARK.md) | Measured limits: frames and signals per second, shortest cycle, trace capacity, latency |

## Hardware and Android references

| Document | Contents |
|---|---|
| [ESP32_PROTOCOL.md](ESP32_PROTOCOL.md) | AutoSim serial protocol v1 (ESP32 node and simulated nodes) |
| [ESP32_MCP2518FD_WIRING.md](ESP32_MCP2518FD_WIRING.md) | Wiring the ESP32 to an MCP2518FD module, bus errors explained |
| [CANABLE_FIRMWARE.md](CANABLE_FIRMWARE.md) | CANable 2.0 firmware: check, flash, recover, the AutoSim bit-timing build |
| [ANDROID_BRIDGE.md](ANDROID_BRIDGE.md) | autosim-bridge on a head unit, ABP v1, auto response |
| [ANDROID_CAN_LAYERS.md](ANDROID_CAN_LAYERS.md) | Android CAN stack from app to hardware, where to simulate ([diagrams](vhal_bridge_architecture.html)) |
| [ANDROID_ENV_CHECKLIST.md](ANDROID_ENV_CHECKLIST.md) | How to check a head unit's CAN environment |
| [VIRTUAL_CAN_PROPOSAL.md](VIRTUAL_CAN_PROPOSAL.md) | Options considered for a virtual CAN channel in Android |

## History

| Document | Note |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Earlier architecture notes; superseded by the design documents above |
| [HOW_TO_RUN.md](HOW_TO_RUN.md) | Earlier run notes; superseded by SETUP and USER_GUIDE |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [MIGRATION.md](MIGRATION.md) | Original plan, moving to a new machine |
