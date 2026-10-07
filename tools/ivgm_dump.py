#!/usr/bin/env python3
"""Read-only register dump for a Felicity IVGM inverter — without Home Assistant.

Inside Home Assistant you don't need this: Settings → Devices & services →
Felicity → ⋮ → **Download diagnostics** produces the same dump through the
integration's own connection.  Use this tool when HA isn't running, or when the
gateway accepts only one Modbus client and you'd rather not stop HA.

Reads every address the IVGM protocol document defines, decodes the shipped
map exactly like the integration, prints a sanity summary and writes one JSON
file to share.

    STRICTLY READ-ONLY: only Modbus function 3 (read holding registers).

Needs Python 3.10+ and pymodbus:   python -m pip install "pymodbus>=3.10"

Examples (from the repository root):

    python tools/ivgm_dump.py --host 192.168.1.50              # Modbus TCP (as the integration)
    python tools/ivgm_dump.py --host 192.168.1.50 --framer rtu # RTU tunnelled over TCP
    python tools/ivgm_dump.py --serial /dev/ttyUSB0            # USB-RS485 (Windows: COM3)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import sys

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "custom_components", "ha_felicity")

_MAPS = {
    "IVGM-8KLP1G1": "_REGISTERS_IVGM_EIGHT",
    "IVGM-15KLP3G1": "_REGISTERS_IVGM_FIFTEEN",
    "IVGM-20KLP3G1": "_REGISTERS_IVGM_TWENTY",
}


def _load(name):
    """Load an HA-free integration module by path (production code, no copy)."""
    spec = importlib.util.spec_from_file_location(f"_felicity_{name}",
                                                  os.path.join(_PKG, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_client(args):
    from pymodbus import FramerType
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient

    if args.serial:
        return ModbusSerialClient(port=args.serial, baudrate=args.baud, bytesize=8,
                                  parity="N", stopbits=1, timeout=args.timeout)
    framer = FramerType.RTU if args.framer == "rtu" else FramerType.SOCKET
    return ModbusTcpClient(host=args.host, port=args.port, framer=framer,
                           timeout=args.timeout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    conn = parser.add_mutually_exclusive_group(required=True)
    conn.add_argument("--host", help="IP of the Modbus TCP gateway / dongle")
    conn.add_argument("--serial", help="serial port of a USB-RS485 adapter")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--framer", choices=("tcp", "rtu"), default="tcp",
                        help="tcp = Modbus TCP (what the integration uses); "
                             "rtu = raw RTU tunnelled over TCP")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--model", choices=sorted(_MAPS), default="IVGM-20KLP3G1")
    parser.add_argument("--out", help="output file (default: ivgm_dump_<time>.json)")
    args = parser.parse_args(argv)

    rd = _load("register_dump")
    register_map = getattr(_load("ivgm"), _MAPS[args.model])
    documented = rd.load_ivgm_documented_addresses()
    reads = rd.plan_reads(set(documented) | rd.map_addresses(register_map))

    client = _make_client(args)
    if not client.connect():
        print("Could not connect.  Check IP/port, and that Home Assistant is not "
              "holding the gateway's only connection.", file=sys.stderr)
        return 2

    def read(address, count):
        try:
            result = client.read_holding_registers(address=address, count=count,
                                                   device_id=args.slave)
        except TypeError:  # pymodbus < 3.10 calls it "slave"
            result = client.read_holding_registers(address=address, count=count,
                                                   slave=args.slave)
        if result.isError():
            raise OSError(str(result))
        return result.registers

    started = _dt.datetime.now().astimezone()
    try:
        raw, failed = rd.read_all(read, reads)
    finally:
        client.close()

    if not any(v is not None for v in raw.values()):
        print("Connected, but every read failed.  Wrong slave id, or try the other "
              "--framer for your gateway.", file=sys.stderr)
        return 3

    dump = rd.build_dump(
        model=args.model, register_map=register_map, raw=raw, failed=failed,
        documented=documented,
        meta={"source": "tools/ivgm_dump.py",
              "captured_at": started.isoformat(timespec="seconds"),
              "connection": "serial" if args.serial else f"tcp/{args.framer}",
              "slave": args.slave},
    )
    out = args.out or f"ivgm_dump_{started:%Y%m%d_%H%M%S}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, indent=1, ensure_ascii=False)

    print("\n".join(rd.summary_lines(dump)))
    print(f"\nWrote {out} — share this file, plus a photo of the inverter display "
          f"taken at the same moment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
