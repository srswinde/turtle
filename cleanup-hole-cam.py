import datetime
from turtle_db import db
import pandas as pd
from pathlib import Path
import tqdm

from sqlalchemy import select, delete
from sqlalchemy import exists
from sqlalchemy.orm import Session


def trysql():
    subq = (
        select(db.hole_camera.timestamp)
        .where(db.hole_camera_resnet_detection.timestamp == db.hole_camera.timestamp)
    )

    stmt = select(db.hole_camera).where(~exists(subq))

    session = db.mksession()
    df = pd.read_sql(stmt, session.connection())
    
    return df

def clean(start_date, dry_run=False):
    """Delete the oldest <=100k hole_camera rows past ``start_date`` that have
    no matching resnet detection (+ their file + metadata). Returns
    ``(ts_end, n_deleted, bytes_freed)`` -- ``ts_end`` is the max timestamp of
    the fetched chunk (``None`` if nothing was found past ``start_date``), so
    a caller can advance ``start_date`` past it on the next call regardless of
    how many rows in this chunk were actually deletable (2026-09-11: added
    for the backlog-purge-hole.py sweep -- verbatim delete logic, unchanged
    for the existing daily-cron call below, which ignores the return value).
    """
    session = db.mksession()
    images_qry = session.query(db.hole_camera) \
        .order_by(db.hole_camera \
        .timestamp.asc()) \
        .filter(db.hole_camera.timestamp > start_date.timestamp()*1e3) \
        .limit(100000)
    data = images_qry.all()
    if not data:
        session.close()
        return None, 0, 0
    ts = datetime.datetime.fromtimestamp( data[0].timestamp/1e3)
    ts_end = datetime.datetime.fromtimestamp( data[-1].timestamp/1e3)

    print(f"cleaning hole cam data during {ts}-{ts_end}")

    metadata_qry = session.query(db.hole_camera_metadata) \
        .filter(db.hole_camera_metadata.timestamp >= ts.timestamp()*1e3) \
        .filter(db.hole_camera_metadata.timestamp < ts_end.timestamp()*1e3) \
        .order_by(db.hole_camera_metadata.timestamp.asc())
    resnet_qry = session.query(db.hole_camera_resnet_detection) \
        .filter(db.hole_camera_resnet_detection.timestamp >= ts.timestamp()*1e3) \
        .filter(db.hole_camera_resnet_detection.timestamp < ts_end.timestamp()*1e3) \
        .order_by(db.hole_camera_resnet_detection.timestamp.asc())

    print(f"Querying data.")
    images = pd.read_sql(images_qry.statement, session.bind)
    resnet = pd.read_sql(resnet_qry.statement, session.bind)


    det_idx = resnet.timestamp.isin(images.timestamp.unique())

    print(f"Attempting to delete {len(images)} images.")
    for rec in metadata_qry:
        if rec.timestamp not in resnet[det_idx]['timestamp'].unique():
            if not dry_run:
                session.delete(rec)
    for rec in resnet_qry:
        if rec.timestamp not in resnet[det_idx]['timestamp'].unique():
            if not dry_run:
                session.delete(rec)
    n_deleted = 0
    bytes_freed = 0
    for rec in tqdm.tqdm(images_qry):
        if rec.timestamp not in resnet[det_idx]['timestamp'].unique():
            path = Path(rec.path)
            if path.exists():
                bytes_freed += path.stat().st_size
                if not dry_run:
                    path.unlink()
            n_deleted += 1
            if not dry_run:
                session.delete(rec)
    if dry_run:
        session.rollback()
    else:
        session.commit()
    session.close()
    return ts_end, n_deleted, bytes_freed
    
def ln(cache=True):

    cpath = Path.home()/"cache.pkl"
    
    if cache and cpath.exists():
        return pd.read_pickle(cpath)

    hc = db.get_dataframe(db.hole_camera)

    res = db.get_dataframe(db.hole_camera_resnet_detection)

    det_idx = hc.timestamp.isin(res.timestamp.unique())

    nodet = hc[~det_idx]

    nodet['tz'] = pd.to_datetime(nodet.timestamp, unit="ms", utc=True)

    nodet['tz'] = nodet['tz'].dt.tz_convert("America/Phoenix")

    
    pd.to_pickle(latenight, cpath)
    return latenight


def delete_chunk(chunk):
    
    for ii, row in chunk.iterrows():
        path = Path(row.path)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
    
    
    session = db.mksession()
    stmt = delete(db.hole_camera).where(db.hole_camera.timestamp.in_(chunk.timestamp))
    session.execute(stmt)
    session.commit()
    session.close()
    
    
def recipe1():
    latenight = ln()

    start = 0
    ndels = 1000
    print(len(latenight))
    for a in tqdm.tqdm(range(200)):
        head = latenight.iloc[start:start+ndels]
        delete_chunk(head)
        
        lasta = a
        start += ndels
        
    print(len(ln(False)))

if __name__ == "__main__":
    now = datetime.datetime.now()
    six_months_ago = now - datetime.timedelta(days=180)
    clean(six_months_ago)
