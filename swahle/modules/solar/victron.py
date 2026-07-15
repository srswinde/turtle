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

# Victron charger device_state codes (partial — the common ones).
DEVICE_STATES = {
    0: "off",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    6: "storage",
    7: "equalize",
    245: "wake-up",
    252: "external control",
}


class VictronDecodeError(ValueError):
    pass


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
