"""Retired: hole-cam IFTTT detection notifications (ported from
shed-cam/ipcamera/notify.py).

No longer wired up (worker.py's run_hole stopped constructing/calling
Notifier) -- the "Cassini is out" IFTTT push now fires from the shed-cam
OWLv2 scanner instead (modules/naturalist/acquisition.py), which is a much
more direct signal than this hole-cam departure heuristic ever was. Left
in place, unused, in case the incident-grouping logic is wanted again.
"""
from __future__ import annotations

import datetime
import logging

import pandas as pd
import requests

from shared.db import SessionLocal
from shared.models import hole_camera, notifications

log = logging.getLogger("swahle.cameras.notify")


class Notifier:
    def __init__(self, *, ifttt_url: str, detect_url: str, wait_seconds: int = 300) -> None:
        self.ifttt_url = ifttt_url
        self.detect_url = detect_url
        self.wait = wait_seconds
        self.last_notification = (
            datetime.datetime.now() - datetime.timedelta(seconds=self.wait + 60)
        )

    def get_days_detection(self, date=None, minprob: float = 0.7, interval: int = 300):
        json_data = {"minprob": minprob, "interval_seconds": interval}
        if date:
            json_data["date"] = str(date)
        try:
            rq = requests.post(self.detect_url, json=json_data, timeout=10)
        except requests.exceptions.ConnectionError as exc:
            log.error("Error connecting to detection server: %s", exc)
            return {}
        if rq.status_code != 200:
            raise RuntimeError(f"{rq.status_code}\n{rq.text}")
        payload = rq.json()
        if not payload:
            return {}
        dfs = []
        for incident, data in payload.items():
            df = pd.DataFrame(data)
            df["incident"] = int(incident)
            dfs.append(df)
        return pd.concat(dfs).groupby("incident").apply(lambda x: x)

    def get_current_state(self):
        session = SessionLocal()
        try:
            return (
                session.query(hole_camera)
                .order_by(hole_camera.timestamp.desc())
                .limit(1)
                .first()
            )
        finally:
            session.close()

    def get_logged_detections(self, date=None):
        if not date:
            date = datetime.datetime.now().replace(hour=0, minute=0, second=0)
        end_date = date + datetime.timedelta(days=1)
        session = SessionLocal()
        try:
            query = (
                session.query(notifications)
                .filter(notifications.timestamp > date.timestamp() * 1e9)
                .filter(notifications.timestamp < end_date.timestamp() * 1e9)
            )
            return pd.read_sql(query.statement, query.session.bind)
        finally:
            session.close()

    def run(self) -> None:
        now = datetime.datetime.now()
        if now - self.last_notification < datetime.timedelta(seconds=self.wait):
            return
        try:
            self.send_notification()
        except Exception as err:  # noqa: BLE001
            log.error("notify failed: %s", err)

    def send_notification(self) -> None:
        self.last_notification = datetime.datetime.now()
        current_state = self.get_current_state()
        logged_detections = self.get_logged_detections()
        days_detection = self.get_days_detection()
        if len(days_detection) == 0:
            return
        incidents = days_detection.index.get_level_values(0).unique()
        if len(incidents) == 0:
            return

        session = SessionLocal()
        try:
            for incident in incidents[:-1]:
                df = days_detection.loc[incident]
                start_time = df.timestamp.min()
                end_time = df.timestamp.max()
                if any(logged_detections.get("detection_start", pd.Series()) == start_time):
                    continue
                session.add(notifications(
                    timestamp=start_time,
                    notification_type="start",
                    detection_start=start_time,
                    detection_end=end_time,
                    message=(
                        f"Detection started at "
                        f"{datetime.datetime.fromtimestamp(start_time / 1e9)}"
                    ),
                ))
            session.commit()

            last_incident = incidents[-1]
            df = days_detection.loc[last_incident]
            last_time = df.timestamp.max()
            start_time = df.timestamp.min()
            start_fmt = datetime.datetime.fromtimestamp(start_time / 1e9).strftime("%H:%M")
            last_fmt = datetime.datetime.fromtimestamp(last_time / 1e9).strftime("%H:%M")
            most_current_fmt = datetime.datetime.fromtimestamp(
                current_state.timestamp / 1e9).strftime("%H:%M")

            if start_time in logged_detections.get("timestamp", pd.Series()).values:
                existing = (
                    session.query(notifications)
                    .filter(notifications.timestamp == start_time)
                    .first()
                )
                if existing:
                    existing.detection_end = last_time
            else:
                if current_state.prob > 0.7:
                    status = (
                        f"{len(incidents)} connections. Cassini last entered the frame "
                        f"at {start_fmt} is still in the frame as of {most_current_fmt}"
                    )
                else:
                    status = f"Cassini left the frame at {last_fmt}"
                best_image = df.iloc[df.prob.idxmax()].url
                session.add(notifications(
                    timestamp=start_time,
                    notification_type="start",
                    detection_start=start_time,
                    detection_end=int(last_time),
                    message=f"Detection started at {start_fmt}",
                ))
                requests.post(
                    self.ifttt_url,
                    json={"image_url": "https://swahle.org" + best_image, "message": status},
                    timeout=10,
                )
                log.info("Sent notification for %s", start_fmt)
            session.commit()
        finally:
            session.close()
