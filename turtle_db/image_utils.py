from pathlib import Path
import pandas as pd
import datetime
import asyncio
import functools

CACHEPATH = Path("/mnt/turtle/cache")
THUMB = CACHEPATH/"thumbnail"
IMGPATH = Path("/mnt/turtle/imgs")


async def collect_thumbnails(start, delta):    
    year = start.strftime("%Y")
    month = start.strftime("%b")
    day = start.strftime("%d")

    path = THUMB/year/month/day
    imgs = collect_images(start, delta)
    thumbs = []

    for ipath in imgs.path:
        th = await make_thumbnail(ipath)
        thumbs.append(th)

    thumbs = pd.DataFrame({"path":thumbs})
    thumbs.index = imgs.index
    imgs['thumbs'] = thumbs
    return imgs

    


async def make_thumbnail(fpath):
    name = fpath.name
    day = fpath.parent.name
    month = fpath.parent.parent.name
    year = fpath.parent.parent.parent.name
    th = THUMB/year/month/day/name
    th.parent.mkdir(parents=True, exist_ok=True)
    if not th.exists():
        cmd = f"convert {str(fpath)} -scale 128x84! {str(th)}"
        proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)


        stdout, stderr = await proc.communicate()

    return th


    
        




def collect_images(start, delta=datetime.timedelta(minutes=30), limit=None):
    year = start.strftime("%Y")
    month = start.strftime("%b")
    day = start.strftime("%d")
    path = IMGPATH/year/month/day
    if path.exists():
        images = [ p for p in path.iterdir() if p.suffix == '.jpg']
        dts = map(
                lambda dt: datetime.datetime.fromtimestamp(int(dt.name.replace(dt.suffix, ''))),
                images
                )
        df = pd.DataFrame({"path":images})
        df.index = dts
        df=df[df.index > start]
        df=df[df.index < (start+delta)]
    else:
        df = pd.DataFrame({"path":[]})
        df.index = pd.DatetimeIndex([])
        
    if limit:
        sl = len(df)//limit

        return df[::sl]
    else:
        return df

async def convert(files, out=None, scale="648x411!"):

    if out is None:
        out = "/mnt/turtle/imgs/2020/Dec/23/test/out.gif"

    cmd = f"convert -dispose previous {' '.join(files)} -scale {scale} -compress LZW -delay 100  -loop 0 \( -clone 0--1 -append -colors 250 -write colormap.png +delete \) -map colormap.png  {out}"
    print(cmd)

    proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    print(stdout, stderr)

    


async def main():
    delta = datetime.timedelta(minutes=6*60)
    start = datetime.datetime(2020, 12, 22, 12, 0, 0)

    files = await collect_thumbnails(start, delta)
    print( files )
    



