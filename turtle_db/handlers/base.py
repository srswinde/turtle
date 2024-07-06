# -*- coding: utf-8 -*-

import tornado.web
import tornado.websocket
from ..db import conditions, get_rand_images, update_image, HAS_TURTLE, get_prob_images
from ..db import mksession, images, probabilities, pretrained, pretrained_20240311_0221
from ..db import detect_intervals
from ..image_utils import collect_images, collect_thumbnails, convert
from sqlalchemy import and_
from sqlalchemy import desc, func
import datetime
import json
import redis
from pathlib import Path
from ..lights import pixels
import os
from dateutil import parser
import pandas as pd
import re

WEBCONNS = []


def get_connections():
    return WEBCONNS


class MainHandler(tornado.web.RequestHandler):

    name = "Home"

    @property
    def navbar(self):
        return [
                ("Home", "/cassini/"),
                ("Gallery", "/cassini/history.html"),
                ("Gifs", "/cassini/gifs.html"),
                ("Lights", "/cassini/lights.html"),
                ("Detect", "/cassini/detect")

                ]

    # @tornado.web.authenticated
    def get(self):

        isMobile = False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        
        try:

            r = redis.Redis()
            data = r.get("turtle_conditions")

            pageinfo = dict(
                       last=json.loads(data),

                    )
        except Exception as error:
            pageinfo = {}

        if isMobile:
            self.render(
                    'index.html',

                    **pageinfo
               )

        else:
            self.render(
                    'index.html',
                    **pageinfo
               )

    def render(self, template, *args, **kwargs):

        isMobile = False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        super().render(
                template,
                *args,
                isMobile=isMobile,
                navbar=self.navbar,
                isActive=self.name,
                **kwargs)


class RecentDetectHandler(MainHandler):

    def get(self):
        ts = self.get_argument("ts", None)
        print(ts)
        prob = self.get_argument("prob", '0')
        prob = float(prob)
        if ts is None:
            ts = datetime.datetime.now().timestamp()
        else:
            ts = int(ts)

        session = mksession()
        qry = session.query(images, pretrained_20240311_0221)\
            .join(images, images.timestamp == pretrained_20240311_0221.timestamp)\
            .filter(images.timestamp > ts-60)\
            .order_by(images.timestamp).limit(9)
        imgs = pd.read_sql(qry.statement, qry.session.bind)
        imgs['url'] = imgs['path']\
            .apply(lambda x: re.sub("\/mnt\/(turtle|nfs)\/imgs", "static/staticturtle", x))
        print(imgs)

        self.finish(
            {'imgs':

                imgs[['prob', 'url', 'timestamp']].to_dict(orient="records")}
        )


class CassiniIntervals(MainHandler):
    name = "Intervals"

    def get(self):
        session = mksession()
        minprob = self.get_argument("minprob", '0.97')
        minprob = float(minprob)
        hoursAgo = self.get_argument("hoursAgo", '24')
        before = datetime.datetime.now() - datetime.timedelta(hours=int(hoursAgo))
        qry = session.query(func.from_unixtime(images.timestamp), images.path, images.hasTurtle, probabilities.prob)\
            .join(images, images.timestamp == probabilities.timestamp)\
            .filter(probabilities.prob > minprob)\
            .filter(images.timestamp > before.timestamp())\
            .filter(images.hasTurtle != HAS_TURTLE.NO)
            
        df = pd.read_sql(qry.statement, qry.session.bind)
        df.index = df.from_unixtime_1
        lowbin = pd.Interval(pd.Timedelta(minutes=0), pd.Timedelta(minutes=5))

        highbin = pd.Interval(pd.Timedelta(minutes=5), pd.Timedelta(minutes=48*60))
        bins = pd.IntervalIndex([lowbin, highbin])
        cut = pd.cut(df.from_unixtime_1.diff(), bins)
        stasis = cut.eq(cut.shift())

        prev_value = False
        new_bins = []
        for time, value in stasis.items():

            if prev_value is False and value is True:
                left = time

            if prev_value is True and value is False:
                idx = df.loc[left:time].prob.argmax()
                ts = df.loc[left:time].iloc[idx].name
                new_bins.append(ts)

            prev_value = value

        if left > ts:
            new_bins.append(left)

        yeses = []
        for detect in new_bins:
            for _,row in df[df['hasTurtle'] == HAS_TURTLE.YES].iterrows():
                if (row.name - detect).total_seconds() > 300 and row.name not in yeses:
                    yeses.append(row.name)
                    
        new_bins.extend(yeses)
        self.finish({"bins": [str(ts) for ts in new_bins]})
        


class HistoryHandler(MainHandler):
    name = "Gallery"

    # @tornado.web.authenticated
    def get(self):

        isMobile = False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        if isMobile:
            self.render('mobile/history.html')
        else:
            self.render('history.html', )


class GifHandler(MainHandler):
    name = 'Gifs'

    # @tornado.web.authenticated
    def get(self):
        gifpath = Path("/mnt/turtle/cache/gifs")
        dirs = list(gifpath.iterdir())[-10:]

        self.render('gifs.html', gifs=reversed(dirs))


class DataTemperatures(MainHandler):

    def get(self, *args):

        if args[0] == 'recent':
            self.write(self.get_recent())

        elif args[0] == 'latest':
            self.write(self.get_last())

    def get_recent(self, minsago=60):

        session = mksession()
        recent = datetime.datetime.now().timestamp() - 24*60*minsago
        resp = session.query(conditions).filter(conditions.timestamp >= recent)
        timestamp, temp, humid = [], [], []
        for row in resp:
            timestamp.append(row.timestamp)
            temp.append(row.temperature_F)
            humid.append(row.relative_humidity)
        return {
                    "timestamp": timestamp,
                    "temp": temp,
                    "humid": humid
                    }

    def get_last(self):

        session = mksession()
        resp = session\
            .query(conditions).order_by(desc(conditions.timestamp)).first()
        return {
                "timestamp": resp.timestamp,
                "temp": resp.temperature_F,
                "humid": resp.relative_humidity
                }


class DataImages(MainHandler):

    def get(self, *args):
        if args[0] == 'recent':
            self.write(self.get_recent())

        elif args[0] == 'latest':
            self.write(self.get_last())

    def get_recent(self, minsago=60):
        session = mksession()
        recent = datetime.datetime.now().timestamp() - 24*60*60
        resp = session.query(images, pretrained_20240311_0221.prob)\
            .join(images, images.timestamp == pretrained_20240311_0221.timestamp)\
            .filter(images.timestamp >= recent)
        df = pd.read_sql(resp.statement, resp.session.bind)
        df['url'] = df['path'].apply(lambda x: str(x).replace("/mnt/turtle", "staticturtle"))
        df['hasTurtle'] = df['hasTurtle'].apply(lambda x: x.value)
        print(df.timestamp)
        resp = {
                "timestamp": df.timestamp.to_list(),
                "url": df.url.to_list(),
                "hasTurtle": df.hasTurtle.to_list(),
                "prob": df.prob.to_list()
        }
        return resp


class Websocket(tornado.websocket.WebSocketHandler):

    def open(self):
        r = redis.Redis()
        data = r.get("turtle_conditions")
        newimage = r.get("home_image")
        alldata = {}
        alldata["home_image"] = json.loads(newimage)
        alldata["home_image"]["name"] = alldata["home_image"]["name"].replace("/mnt/turtle", "staticturtle")
        self.write_message(json.dumps(alldata))

        WEBCONNS.append(self)

    def on_close(self):

        WEBCONNS.remove(self)


class ImageSocket(tornado.websocket.WebSocketHandler):

    def open(self):
        print("WebSocket opened")

    async def on_message(self, message):
        error = {}
        resp = {}

        try:
            data = json.loads(message)
        except ValueError:
            error["badJSON"] = "Malformed JSON"
            self.write_message(error)
            return

        action = data['action']
        if "action" not in data:
            error["noAction"] = "Missing action keyword"
            self.write_message(error)

        if action == "collect":
            start = data['start']
            delta = data['delta']
            limit = None
            if 'limit' in data:
                limit = data['limit']

            start = datetime.datetime(**start)
            delta = datetime.timedelta(**delta)
            imgs = await collect_thumbnails(start, delta)
            urls = imgs.applymap(
                    self.to_url,
                    )
            urls["timestamp"] = urls.index
            if limit:
                sl = len(urls)//limit
                jsondata = json.loads(
                        urls[::sl].to_json(
                            default_handler=str,
                            orient="records"
                            ))

            else:
                jsondata = json.loads(
                        urls.to_json(
                            default_handler=str,
                            orient="records"

                            ))

            resp["action"] = action
            resp["error"] = error
            resp["imgs"] = jsondata
            self.write_message(json.dumps(resp))

        elif action == "gifify":
            limit = 20
            if "limit" in data:
                limit = int(data["limit"])

            t0 = data['timestamps'][0]["timestamp"]
            t1 = data['timestamps'][1]["timestamp"]
            MST = datetime.timedelta(hours=7)
            t0 = datetime.datetime.fromtimestamp(t0/1000) + MST
            t1 = datetime.datetime.fromtimestamp(t1/1000) + MST
            imgs = collect_images(t0, t1-t0)
            sl = len(imgs)//limit
            if sl < 1:
                sl = 1
            files = [str(imgs) for imgs in imgs.path[::sl]]
            t0string = t0.strftime("%Y%m%dT%H%M%S")
            t1string = t1.strftime("%Y%m%dT%H%M%S")
            outfile = Path(f"/mnt/turtle/cache/gifs/{t0string}_{t1string}.gif")

            if outfile.exists():
                resp["action"] = action
                resp["error"] = error
                resp["url"] = str(outfile)\
                    .replace("/mnt/turtle", "staticturtle")
                self.write_message(resp)

            else:
                outfile.parent.mkdir(parents=True, exist_ok=True)

                print("converting")
                await convert(files, outfile)
                print(outfile)
                resp["action"] = action
                resp["error"] = error
                resp["url"] = str(outfile)\
                    .replace("/mnt/turtle", "staticturtle")
                self.write_message(resp)

        else:
            error['badAction'] = "No such action" + action
            self.write_message({"error": "no such action "+action})

    def on_close(self):
        print("WebSocket closed")

    def to_url(self, path):
        imgurl = str(path).replace("/mnt/turtle", "staticturtle")
        return imgurl


class ImagesPlayground(MainHandler):

    def post(self):
        data = json.loads(self.request.body)
        date = parser.parse(data['date'])
        groups = detect_intervals(date)
        
       
        print(groups.index)
        group_dict = {}
        for group in groups.index.get_level_values(0).unique():
            group_dict[group] = dict(
                timestamp = groups.loc[group].index.astype(int).to_list(),
                prob = list(groups.loc[group].values)
            )
                
                
            
        self.write(group_dict)
        
        
    def get(self):

        self.render('images-playground.html')

class Lights(MainHandler):
    name = "Lights"
    p = pixels()

    async def get(self):
        r = self.get_argument('r', None)
        g = self.get_argument('g', None)
        b = self.get_argument('b', None)
        w = self.get_argument('w', None)

        print('We got data')

        if r:
            await self.p.r(int(r))
        if g:
            await self.p.g(int(g))
        if b:
            await self.p.b(int(b))
        if w:
            await self.p.w(int(w))

        self.render(
                    'lights.html',

               )


class DetectHandler(MainHandler):

    name = "Detect"

    async def get(self):
        argre = re.compile(r"\d{8,12}")
        dt = None
        for arg in self.request.arguments:
            if argre.match(arg) and self.get_argument(arg) in ["0", "1"]:
                hasTurtle = int(self.get_argument(arg))
                dt = datetime.datetime.fromtimestamp(int(arg))
                if hasTurtle:
                    hasTurtle = HAS_TURTLE.YES
                else:
                    hasTurtle = HAS_TURTLE.NO

                #print(dt, hasTurtle)
                update_image(int(arg), hasTurtle)


        prob_low = self.get_argument('prob_low', '')
        prob_high = self.get_argument('prob_high', '')
        if prob_low:
            prob_low = float(prob_low)
        else: 
            prob_low = 0.5
        if prob_high:
            prob_high = float(prob_high)
        else:
            prob_high = 1.0
        
        nimages = self.get_argument('nimages', None)
        since = self.get_argument('since', None)
        if since:
            print(since)
            since = parser.parse(since, default=datetime.datetime.now()-datetime.timedelta(hours=1))
            
        else:
            print("NOT HERE")
            if dt is None:
                since = datetime.datetime.now() - datetime.timedelta(hours=1500)
            else:
                since = dt
                
        print("since is ", since)
        since = since.timestamp()
        
        if nimages:
            nimages = int(nimages)
        else:
            nimages = 20
        imgs = get_prob_images(prob_low, prob_high, nimages, null=True, since=since)
        
        imgs['url'] = imgs['path'].apply(lambda x: str(x).replace("/mnt/turtle", "staticturtle"))
        print(imgs.tail())
        self.render(
                'detect.html',
                imgs=imgs,
                )

class EdgeCaseHandler(MainHandler):
    
    def get(self):
        
        argre = re.compile(r"\d{8,12}")

        for arg in self.request.arguments:
            if argre.match(arg) and self.get_argument(arg) in ["0", "1"]:
                hasTurtle = int(self.get_argument(arg))
                dt = datetime.datetime.fromtimestamp(int(arg))
                if hasTurtle:
                    hasTurtle = HAS_TURTLE.YES
                else:
                    hasTurtle = HAS_TURTLE.NO

                print(dt, hasTurtle)
                update_image(int(arg), hasTurtle)
        
        session = mksession()
        qry = session.query(images, pretrained)\
            .join(images, images.timestamp == pretrained.timestamp)\
            .filter(images.hasTurtle == HAS_TURTLE.NO)\
            .filter(pretrained.prob > 0.70)\
            .order_by(pretrained.prob.desc())\
            .limit(400)
        imgs = pd.read_sql(qry.statement, qry.session.bind)
        imgs['url'] = imgs['path']\
            .apply(lambda x: str(x).replace("/mnt/turtle", "staticturtle"))
    

        imgs.index = pd.to_datetime(imgs.timestamp, unit='s')
        self.render(
            'detect.html',
            imgs=imgs
        )

class ImageHandler(tornado.web.StaticFileHandler):
    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.dirname = "/mnt/turtle/imgs/"

    def get_absolute_path(self, root, path):
        return os.path.join(self.dirname, path)
