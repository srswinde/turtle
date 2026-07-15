"""Solar telemetry table — kept on its own declarative metadata (like the
naturalist perception tables) so we never touch the legacy ``turtle_db.db``
schema shared with the Tornado app. ``ensure_tables()`` creates it idempotently
(CREATE TABLE IF NOT EXISTS semantics) at module startup.
"""
from __future__ import annotations

import time

from sqlalchemy import BigInteger, Column, Float, Integer, String
from sqlalchemy.orm import declarative_base

from shared.db import SessionLocal, engine

SolarBase = declarative_base()


class solar_reading(SolarBase):
    __tablename__ = "solar_reading"
    id = Column(Integer, primary_key=True, autoincrement=True)
    device = Column(String(32), index=True)
    # Unix ms, when this app received the reading. BigInteger (not Float/Double) —
    # a MySQL FLOAT is 4-byte single precision (~7 sig figs) and a unix-seconds
    # timestamp already consumes all of those, so every row would round to the
    # same value and collapse onto one x position in any time-series chart.
    ts = Column(BigInteger, index=True)
    model_id = Column(Integer)
    nonce = Column(Integer)
    device_state = Column(Integer)
    device_state_name = Column(String(32))
    charger_error = Column(Integer)
    battery_voltage = Column(Float)
    battery_current = Column(Float)
    yield_today = Column(Float)
    pv_power = Column(Float)
    load_current = Column(Float, nullable=True)
    rssi = Column(Integer, nullable=True)


def ensure_tables() -> None:
    SolarBase.metadata.create_all(engine, checkfirst=True)


def _row_to_dict(row: solar_reading) -> dict:
    return {
        "device": row.device,
        "ts": row.ts,
        "model_id": row.model_id,
        "nonce": row.nonce,
        "device_state": row.device_state,
        "device_state_name": row.device_state_name,
        "charger_error": row.charger_error,
        "battery_voltage": row.battery_voltage,
        "battery_current": row.battery_current,
        "battery_power": round(row.battery_voltage * row.battery_current, 2),
        "yield_today": row.yield_today,
        "pv_power": row.pv_power,
        "load_current": row.load_current,
        "rssi": row.rssi,
    }


def save_reading(device: str, reading: dict) -> dict:
    session = SessionLocal()
    try:
        row = solar_reading(device=device, ts=int(time.time() * 1000), **reading)
        session.add(row)
        session.commit()
        return _row_to_dict(row)
    finally:
        session.close()


def latest_reading(device: str) -> dict | None:
    session = SessionLocal()
    try:
        row = (
            session.query(solar_reading)
            .filter(solar_reading.device == device)
            .order_by(solar_reading.ts.desc())
            .first()
        )
        return _row_to_dict(row) if row else None
    finally:
        session.close()


def readings_since(device: str, since_ts_ms: int) -> list[dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(solar_reading)
            .filter(solar_reading.device == device)
            .filter(solar_reading.ts >= since_ts_ms)
            .order_by(solar_reading.ts.asc())
            .all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()
