from PIL import Image
from pathlib import Path
from turtle_db import db
import pandas as pd
import datetime
import shutil
import time
from sqlalchemy import delete, func, or_
import tqdm


def save_compress(path, reduce_factor, quality, pil_image):
    """
    Save the image in a compressed format.
    """
    
        
    w, h = pil_image.size
    new_size = (int(w / reduce_factor), int(h / reduce_factor))
    rimg = pil_image.resize(new_size, Image.LANCZOS)
    rimg.save(path, format='JPEG', quality=quality)
    

def clean_overtime(ts, hours, dry_run=False):
    session = db.mksession()
    ts_end = ts + datetime.timedelta(hours=hours)
    ts_start_ms = int(ts.timestamp() * 1e3)
    ts_end_ms = int(ts_end.timestamp() * 1e3)

    image_ts_rows = session.query(db.shed_camera.timestamp) \
        .filter(db.shed_camera.timestamp >= ts_start_ms) \
        .filter(db.shed_camera.timestamp < ts_end_ms) \
        .distinct() \
        .all()

    if not image_ts_rows:
        print(f"No shed camera images between {ts} and {ts_end}")
        session.close()
        return 0, 0

    keeper_rows = session.query(db.shed_camera_resnet_detection.timestamp) \
        .filter(db.shed_camera_resnet_detection.timestamp >= ts_start_ms) \
        .filter(db.shed_camera_resnet_detection.timestamp < ts_end_ms) \
        .filter(
            or_(
                db.shed_camera_resnet_detection.class_name.is_(None),
                func.lower(db.shed_camera_resnet_detection.class_name) != "chair",
            )
        ) \
        .distinct() \
        .all()

    all_ts = {row[0] for row in image_ts_rows}
    keepers = {row[0] for row in keeper_rows}
    losers = list(all_ts - keepers)

    if not losers:
        if dry_run:
            print(f"[dry-run] No rows to delete between {ts} and {ts_end}")
        session.close()
        return 0, 0

    if dry_run:
        print(
            f"[dry-run] Would delete {len(losers)} timestamps "
            f"between {ts} and {ts_end}"
        )

    loser_set = set(losers)
    image_rows = session.query(db.shed_camera.timestamp, db.shed_camera.path) \
        .filter(db.shed_camera.timestamp.in_(losers)) \
        .all()

    missing_files = 0
    files_to_delete = 0
    bytes_freed = 0

    for img_ts, img_path in image_rows:
        if img_ts in loser_set:
            path = Path(img_path)
            if path.exists():
                size = path.stat().st_size
                if dry_run:
                    files_to_delete += 1
                    bytes_freed += size
                else:
                    print(f"Deleting image file {path}")
                    bytes_freed += size
                    path.unlink()
            else:
                missing_files += 1

    if dry_run:
        print(
            f"[dry-run] Would delete {files_to_delete} files "
            f"({missing_files} already missing, {bytes_freed/1e9:.2f}GB)"
        )
        session.close()
        return len(losers), bytes_freed

    chunk_size = 1000
    for i in range(0, len(losers), chunk_size):
        chunk = losers[i:i + chunk_size]
        session.execute(
            delete(db.shed_camera_metadata).where(db.shed_camera_metadata.timestamp.in_(chunk))
        )
        session.execute(
            delete(db.shed_camera_resnet_detection).where(db.shed_camera_resnet_detection.timestamp.in_(chunk))
        )
        session.execute(
            delete(db.shed_camera).where(db.shed_camera.timestamp.in_(chunk))
        )

    session.commit()
    session.close()
    return len(losers), bytes_freed


def collect_resnet():
    session = db.mksession()
    while 1:
        qry = session.query(db.shed_camera_resnet_detection).filter(db.shed_camera_resnet_detection.class_number == 64).limit(10000)
        for rec in qry:

            meta = session.query(db.shed_camera_metadata).filter(db.shed_camera_metadata.timestamp == rec.timestamp).all()
            image = session.query(db.shed_camera).filter(db.shed_camera.timestamp == rec.timestamp).all()
            resnet = session.query(db.shed_camera_resnet_detection).filter(db.shed_camera_resnet_detection.timestamp == rec.timestamp).all()
            detections = set([r.class_name for r in resnet])
            if len(detections) == 1:
                print(Path(rec.path))
                session.delete(rec)
                session.delete(image[0])
                session.delete(meta[0])
                for r in resnet:
                    session.delete(r)

                session.commit()
                Path(rec.path).unlink()

            
def collect_images():
    session = db.mksession()
    qry = session.query(db.shed_camera).order_by(db.shed_camera.timestamp.desc()).limit(100)
    for rec in qry:
        meta = session.query(db.shed_camera_metadata).filter(db.shed_camera_metadata.timestamp == rec.timestamp).all()
        resnet = session.query(db.shed_camera_resnet_detection).filter(db.shed_camera_resnet_detection.timestamp == rec.timestamp).all()
        print(rec.path)
        print(meta, resnet)


def collect_images_in_range(start_ts, end_ts, output_dir=None):
    """
    Collect shed camera images for [start_ts, end_ts).

    If output_dir is provided, files are copied there.
    Returns a pandas DataFrame with one row per collected image.
    """
    if end_ts <= start_ts:
        raise ValueError("end_ts must be greater than start_ts")

    session = db.mksession()
    start_ms = int(start_ts.timestamp() * 1e3)
    end_ms = int(end_ts.timestamp() * 1e3)

    qry = session.query(db.shed_camera.timestamp, db.shed_camera.path) \
        .filter(db.shed_camera.timestamp >= start_ms) \
        .filter(db.shed_camera.timestamp < end_ms) \
        .order_by(db.shed_camera.timestamp.asc())

    out_path = None
    if output_dir is not None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

    collected_rows = []
    missing = 0
    for ts_ms, img_path in qry:
        src = Path(img_path)
        if not src.exists():
            missing += 1
            continue

        if out_path is None:
            collected_rows.append(
                {
                    "timestamp": ts_ms,
                    "source_path": str(src),
                    "copied_path": None,
                }
            )
            continue

        # Prefix with timestamp to reduce name collisions when flattening directories.
        dest = out_path / f"{ts_ms}_{src.name}"
        shutil.copy2(src, dest)
        collected_rows.append(
            {
                "timestamp": ts_ms,
                "source_path": str(src),
                "copied_path": str(dest),
            }
        )

    session.close()
    collected_df = pd.DataFrame(collected_rows)
    print(
        f"Collected {len(collected_df)} images between {start_ts} and {end_ts}"
        f" (missing files: {missing})"
    )
    return collected_df


def collect_image_detections_in_range(start_ts, end_ts, aggregate=True, profile=False):
    """
    Collect shed camera images and their ResNet detections for [start_ts, end_ts).

    aggregate=True: one row per image with a `detections` list of dicts.
    aggregate=False: one row per (image, detection) pair.
    profile=True: print timing breakdown by stage.
    """
    if end_ts <= start_ts:
        raise ValueError("end_ts must be greater than start_ts")

    session = db.mksession()
    start_ms = int(start_ts.timestamp() * 1e3)
    end_ms = int(end_ts.timestamp() * 1e3)

    t0 = time.perf_counter()
    t_last = t0

    def mark(stage):
        nonlocal t_last
        if not profile:
            return
        now = time.perf_counter()
        print(f"[timing] {stage}: {now - t_last:.4f}s")
        t_last = now

    images_qry = session.query(
        db.shed_camera.timestamp.label("timestamp"),
        db.shed_camera.path.label("image_path"),
    ).filter(
        db.shed_camera.timestamp >= start_ms
    ).filter(
        db.shed_camera.timestamp < end_ms
    ).order_by(
        db.shed_camera.timestamp.asc()
    )

    images_df = pd.read_sql(images_qry.statement, session.bind)
    mark(f"images query rows={len(images_df)}")
    if images_df.empty:
        session.close()
        mark("session close")
        if profile:
            print(f"[timing] total: {time.perf_counter() - t0:.4f}s")
        if aggregate:
            return pd.DataFrame(columns=["timestamp", "image_path", "detections"])
        return pd.DataFrame(
            columns=[
                "timestamp",
                "image_path",
                "detection_index",
                "model_name",
                "detection_path",
                "prob",
                "class_name",
                "class_number",
                "x1",
                "y1",
                "x2",
                "y2",
            ]
        )

    timestamps = images_df["timestamp"].tolist()
    det_parts = []
    chunk_size = 5000
    for i in range(0, len(timestamps), chunk_size):
        chunk = timestamps[i:i + chunk_size]
        det_qry = session.query(
            db.shed_camera_resnet_detection.timestamp.label("timestamp"),
            db.shed_camera_resnet_detection.index.label("detection_index"),
            db.shed_camera_resnet_detection.model_name.label("model_name"),
            db.shed_camera_resnet_detection.path.label("detection_path"),
            db.shed_camera_resnet_detection.prob.label("prob"),
            db.shed_camera_resnet_detection.class_name.label("class_name"),
            db.shed_camera_resnet_detection.class_number.label("class_number"),
            db.shed_camera_resnet_detection.x1.label("x1"),
            db.shed_camera_resnet_detection.y1.label("y1"),
            db.shed_camera_resnet_detection.x2.label("x2"),
            db.shed_camera_resnet_detection.y2.label("y2"),
        ).filter(db.shed_camera_resnet_detection.timestamp.in_(chunk)).order_by(
            db.shed_camera_resnet_detection.timestamp.asc(),
            db.shed_camera_resnet_detection.index.asc(),
        )
        det_parts.append(pd.read_sql(det_qry.statement, session.bind))
    mark(f"detections queries chunks={len(det_parts)}")

    session.close()
    mark("session close")

    if det_parts:
        detections_df = pd.concat(det_parts, ignore_index=True)
    else:
        detections_df = pd.DataFrame(columns=["timestamp"])
    mark(f"detections concat rows={len(detections_df)}")

    if not aggregate:
        out = images_df.merge(detections_df, on="timestamp", how="left")
        mark(f"merge output rows={len(out)}")
        if profile:
            print(f"[timing] total: {time.perf_counter() - t0:.4f}s")
        return out

    det_map = {}
    if not detections_df.empty:
        for row in detections_df.itertuples(index=False):
            det_map.setdefault(row.timestamp, []).append(
                {
                    "detection_index": row.detection_index,
                    "model_name": row.model_name,
                    "detection_path": row.detection_path,
                    "prob": row.prob,
                    "class_name": row.class_name,
                    "class_number": row.class_number,
                    "x1": row.x1,
                    "y1": row.y1,
                    "x2": row.x2,
                    "y2": row.y2,
                }
            )

    out = images_df.copy()
    out["detections"] = out["timestamp"].map(lambda ts: det_map.get(ts, []))
    mark(f"aggregate output rows={len(out)}")
    if profile:
        print(f"[timing] total: {time.perf_counter() - t0:.4f}s")
    return out


def compress():    
    shed_dir = "/mnt/nfs/shed-cam"
    ii = 0
    for imgfile in Path(shed_dir).rglob("2025/5/**/*.jpg"):
        size = imgfile.stat().st_size
        
        ii+= 1
        if ii % 100 == 0:
            print(f"Last processed {imgfile}")
            ii = 0
        #print(f"processing {imgfile}")
        try:
            img = Image.open(imgfile)
            save_compress(imgfile, reduce_factor=3, quality=75, pil_image=img)
            img.close()
            
        except (IOError):
            print(f"Deleting corrupted image: {imgfile}")
            if not imgfile.is_dir():
                imgfile.unlink()
            
 

    
if __name__ == "__main__":
    now = datetime.datetime.now()
    time_ago = now - datetime.timedelta(days=270)
    period = pd.date_range(time_ago, periods=24*90, freq="1h")
    for ts in tqdm.tqdm(period):
        clean_overtime(ts.to_pydatetime(), 1)