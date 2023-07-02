# -*- coding: utf-8 -*-

import tornado.web
import tornado.websocket
from ..db import conditions, get_rand_images, update_image, HAS_TURTLE
from ..image_utils import collect_images, collect_thumbnails, convert
from sqlalchemy.orm import sessionmaker
from sqlalchemy import desc
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
    name="Home"

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

        isMobile=False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        r = redis.Redis()
        data = r.get("turtle_conditions")

        pageinfo = dict(
                   last=json.loads(data), 
                )

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

        isMobile=False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        super().render(
                template, 
                *args, 
                isMobile=isMobile, 
                navbar=self.navbar, 
                isActive=self.name, 
                **kwargs)



class HistoryHandler(MainHandler):
    name = "Gallery"

    # @tornado.web.authenticated
    def get(self): 

        isMobile=False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        if isMobile:
            self.render('mobile/history.html' )
        else:
            self.render('history.html', )



class GifHandler(MainHandler):
    name='Gifs'

    # @tornado.web.authenticated
    def get(self):
        gifpath = Path("/mnt/turtle/cache/gifs")
        dirs = list(gifpath.iterdir())[-10:]

        self.render('gifs.html', gifs=reversed(dirs) )

class Data(MainHandler):


    def get(self, *args):

        if args[0] == 'recent':
            self.write( self.get_recent() )

        elif args[0] == 'last':
            self.write( self.get_last() )


    def get_recent(self, minsago=60):

        session=sessionmaker(bind=conditions.metadata.bind)()
        recent = datetime.datetime.now().timestamp() - 24*60*minsago
        resp=session.query(conditions).filter(conditions.timestamp >= recent)
        timestamp,temp, humid = [],[],[]
        for row in resp:
            timestamp.append(row.timestamp)
            temp.append(row.temperature_F)
            humid.append(row.relative_humidity)
        return {
                    "timestamp":timestamp,
                    "temp": temp,
                    "humid":humid
                    }


    def get_last(self):
        session=sessionmaker(bind=conditions.metadata.bind)()
        resp=session.query(conditions).order_by(desc(conditions.timestamp)).first()
        return { 
                "timestamp":resp.timestamp,
                "temp": resp.temperature_F,
                "humid":resp.relative_humidity
                }



class Websocket(tornado.websocket.WebSocketHandler):

    def open(self):
        r = redis.Redis()
        data = r.get("turtle_conditions")
        newimage = r.get("home_image")
        print(f"newimage is {newimage}")
        alldata = {"temp":json.loads(data)}
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
                jsondata=json.loads(
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

            t0=data['timestamps'][0]["timestamp"]
            t1=data['timestamps'][1]["timestamp"]
            MST = datetime.timedelta(hours=7)
            t0=datetime.datetime.fromtimestamp(t0/1000) + MST
            t1=datetime.datetime.fromtimestamp(t1/1000) + MST
            imgs = collect_images(t0, t1-t0)
            sl = len(imgs)//limit
            if sl < 1:
                sl=1
            files = [str(imgs) for imgs in imgs.path[::sl]]
            t0string = t0.strftime("%Y%m%dT%H%M%S")
            t1string = t1.strftime("%Y%m%dT%H%M%S")
            outfile = Path(f"/mnt/turtle/cache/gifs/{t0string}_{t1string}.gif")

            if outfile.exists():
                resp["action"] = action
                resp["error"] = error
                resp["url"] = str(outfile).replace("/mnt/turtle", "staticturtle")
                self.write_message(resp)
            
            else:
                outfile.parent.mkdir(parents=True, exist_ok=True)

                print("converting")
                await convert(files, outfile)
                print(outfile)
                resp["action"] = action
                resp["error"] = error
                resp["url"] = str(outfile).replace("/mnt/turtle", "staticturtle")
                self.write_message(resp)



            

            

        else:
            error['badAction'] = "No such action" + action
            self.write_message({"error": "no such action "+action})

    def on_close(self):
        print("WebSocket closed")

    def to_url(self, path):
        imgurl = str(path).replace("/mnt/turtle", "staticturtle")
        return imgurl


class Test(MainHandler):

    def get(self):
        
        self.write(dict(self.request.headers))

class Lights(MainHandler):
    name = "Lights"
    p=pixels()
    async def get(self):
        r=self.get_argument('r', None)
        g=self.get_argument('g', None)
        b=self.get_argument('b', None)
        w=self.get_argument('w', None)


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
            else:
                print(dt, self.get_argument(arg), "bad")
                
                
        imgs = get_rand_images()
        imgs['url'] = imgs['path'].apply(lambda x: str(x).replace("/mnt/turtle", "staticturtle"))
        
        self.render(
                'detect.html',
                imgs=imgs,
        
                )

class ImageHandler(tornado.web.StaticFileHandler):
    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.dirname = "/mnt/turtle/imgs/"
        
    def get_absolute_path(self, root, path):
        return os.path.join(self.dirname, path)
