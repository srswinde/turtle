"""Decode Victron "Instant Readout" BLE manufacturer data.

Victron's encrypted broadcast telemetry (SmartSolar MPPT, BMV, etc.): AES-128-CTR
with a per-device key (from VictronConnect); the IV is the little-endian 2-byte
nonce/counter from the packet, zero-padded to 16 bytes. Field layout below was
derived empirically from a live SmartSolar MPPT 100/50 capture and cross-checked
by confirming the decrypted plaintext is stable across many packets that share
the same underlying state (only the nonce/ciphertext differ) — that consistency
is only possible with the correct key and IV construction.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

VICTRON_VENDOR_ID = 0x02E1
PRODUCT_ADVERTISEMENT = 0x10

# Victron charger device_state codes (VE.Direct "OperationMode" — the common ones).
DEVICE_STATES = {
    0: "off",
    1: "low power",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    6: "storage",
    7: "equalize",
    9: "inverting",
    11: "power supply",
    245: "starting up",
    246: "repeated absorption",
    247: "recondition",
    248: "battery safe",
    249: "active",
    252: "external control",
}

# Victron battery-monitor aux input mode (BatteryMonitor plaintext, bits 66-67).
AUX_MODES = {0: "starter voltage", 1: "midpoint voltage", 2: "temperature", 3: "disabled"}


class VictronDecodeError(ValueError):
    pass


class BitReader:
    """LSB-first bit reader for Victron's bit-packed battery-monitor record.

    Field layout (and this reader) matches the community-verified
    keshavdv/victron-ble project (devices/base.py + devices/battery_monitor.py),
    cross-checked here against a live SmartShunt capture: the decoded voltage
    tracked the independently-known battery-bank voltage from the SmartSolar
    MPPT on the same bank, and stayed stable/plausible across many packets.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._index = 0

    def read_bit(self) -> int:
        bit = (self._data[self._index >> 3] >> (self._index & 7)) & 1
        self._index += 1
        return bit

    def read_unsigned_int(self, num_bits: int) -> int:
        value = 0
        for position in range(num_bits):
            value |= self.read_bit() << position
        return value

    def read_signed_int(self, num_bits: int) -> int:
        value = self.read_unsigned_int(num_bits)
        return value - (1 << num_bits) if value & (1 << (num_bits - 1)) else value


@dataclass
class BatteryMonitorReading:
    model_id: int
    nonce: int
    remaining_mins: int | None
    voltage: float | None
    alarm: int
    aux_mode: int
    aux_mode_name: str
    current: float | None
    consumed_ah: float | None
    soc: float | None


@dataclass
class SolarChargerReading:
    model_id: int
    nonce: int
    device_state: int
    device_state_name: str
    charger_error: int
    battery_voltage: float
    battery_current: float
    yield_today: float
    pv_power: float
    load_current: float | None


def decrypt_manufacturer_data(mfg: bytes, key: bytes) -> bytes:
    """Decrypt the encrypted tail of a Victron manufacturer-data field.

    ``mfg`` is the raw bytes of the 0xFF AD structure value (vendor id onward).
    Returns the decrypted plaintext; layout is device-type specific.
    """
    if len(mfg) < 11:
        raise VictronDecodeError(f"manufacturer data too short ({len(mfg)} bytes)")

    vendor_id = mfg[0] | (mfg[1] << 8)
    if vendor_id != VICTRON_VENDOR_ID:
        raise VictronDecodeError(f"not a Victron vendor id: {vendor_id:#06x}")
    if mfg[2] != PRODUCT_ADVERTISEMENT:
        raise VictronDecodeError(f"unexpected record type: {mfg[2]:#04x}")

    # mfg[5:7] is present but unidentified (constant in every capture) — not
    # needed to locate the nonce/key-check/ciphertext that follow it.
    nonce = mfg[7] | (mfg[8] << 8)
    key_check = mfg[9]
    ciphertext = mfg[10:]

    if key_check != key[0]:
        raise VictronDecodeError("encryption key does not match this device")

    iv = struct.pack("<H", nonce) + b"\x00" * 14
    decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def parse_solar_charger(plaintext: bytes, *, model_id: int, nonce: int) -> SolarChargerReading:
    if len(plaintext) < 10:
        raise VictronDecodeError(f"decrypted payload too short ({len(plaintext)} bytes)")

    device_state, charger_error = plaintext[0], plaintext[1]
    battery_voltage = struct.unpack("<h", plaintext[2:4])[0] / 100.0
    battery_current = struct.unpack("<h", plaintext[4:6])[0] / 10.0
    yield_today = struct.unpack("<H", plaintext[6:8])[0] * 0.01
    pv_power = float(struct.unpack("<H", plaintext[8:10])[0])

    load_current = None
    if len(plaintext) >= 12:
        load_raw = struct.unpack("<H", plaintext[10:12])[0]
        if load_raw != 0xFFFF:  # sentinel: model has no load output
            load_current = (load_raw & 0x1FF) / 10.0

    return SolarChargerReading(
        model_id=model_id,
        nonce=nonce,
        device_state=device_state,
        device_state_name=DEVICE_STATES.get(device_state, f"unknown({device_state})"),
        charger_error=charger_error,
        battery_voltage=battery_voltage,
        battery_current=battery_current,
        yield_today=yield_today,
        pv_power=pv_power,
        load_current=load_current,
    )


def parse_battery_monitor(plaintext: bytes, *, model_id: int, nonce: int) -> BatteryMonitorReading:
    if len(plaintext) < 15:
        raise VictronDecodeError(f"decrypted payload too short ({len(plaintext)} bytes)")

    reader = BitReader(plaintext)
    remaining_mins = reader.read_unsigned_int(16)
    voltage = reader.read_signed_int(16)
    alarm = reader.read_unsigned_int(16)
    aux = reader.read_unsigned_int(16)
    aux_mode = reader.read_unsigned_int(2)
    current = reader.read_signed_int(22)
    consumed_ah = reader.read_unsigned_int(20)
    soc = reader.read_unsigned_int(10)

    return BatteryMonitorReading(
        model_id=model_id,
        nonce=nonce,
        remaining_mins=remaining_mins if remaining_mins != 0xFFFF else None,
        voltage=voltage / 100.0 if voltage != 0x7FFF else None,
        alarm=alarm,
        aux_mode=aux_mode,
        aux_mode_name=AUX_MODES.get(aux_mode, f"unknown({aux_mode})"),
        current=current / 1000.0 if current != 0x3FFFFF else None,
        consumed_ah=-consumed_ah / 10.0 if consumed_ah != 0xFFFFF else None,
        soc=soc / 10.0 if soc != 0x3FF else None,
    )
