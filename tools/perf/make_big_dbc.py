"""Write a large synthetic CAN FD database for UI benchmarks.

    python tools/perf/make_big_dbc.py OUT.dbc [--messages 1500] [--signals 16]

Each message is CAN FD, 64 bytes, with `signals` 32-bit Intel signals (factor, offset, unit, range); every fourth
signal has a value table. 1500 x 16 = 24 000 signals - a large vehicle database.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def build(n_msg: int, n_sig: int) -> str:
    out = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU1 ECU2 ECU3 ECU4", ""]
    vals, attrs = [], []
    for m in range(n_msg):
        can_id = 0x100 + m
        name = f"MSG_{m:04d}_{['Body', 'Chassis', 'Powertrain', 'ADAS'][m % 4]}"
        out.append(f"BO_ {can_id} {name}: 64 ECU{m % 4 + 1}")
        for s in range(n_sig):
            sig = f"{name[:8]}_Sig{s:02d}_{['Speed', 'Temp', 'State', 'Torque'][s % 4]}"
            start = s * 32
            if s % 4 == 2:
                out.append(f' SG_ {sig} : {start}|32@1+ (1,0) [0|3] "" ECU1')
                vals.append(f'VAL_ {can_id} {sig} 0 "Off" 1 "On" 2 "Error" 3 "Init" ;')
            else:
                out.append(f' SG_ {sig} : {start}|32@1- (0.01,-100) [-100|1000] "km/h" ECU1')
        out.append("")
        attrs.append(f'BA_ "VFrameFormat" BO_ {can_id} 14;')
    out += ['BA_DEF_ BO_  "VFrameFormat" ENUM  "StandardCAN","ExtendedCAN","reserved","reserved","reserved","reserved",'
            '"reserved","reserved","reserved","reserved","reserved","reserved","reserved","reserved","StandardCAN_FD",'
            '"ExtendedCAN_FD";',
            'BA_DEF_  "BusType" STRING ;', 'BA_DEF_DEF_  "VFrameFormat" "StandardCAN";', 'BA_DEF_DEF_  "BusType" "CAN FD";',
            'BA_ "BusType" "CAN FD";', *attrs, "", *vals, ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--messages", type=int, default=1500)
    ap.add_argument("--signals", type=int, default=16)
    a = ap.parse_args()
    Path(a.out).write_text(build(a.messages, a.signals), encoding="utf-8")
    print(f"{a.out}: {a.messages} messages, {a.messages * a.signals} signals")


if __name__ == "__main__":
    main()
