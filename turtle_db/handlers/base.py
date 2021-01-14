# -*- coding: utf-8 -*-

import tornado.web
import tornado.websocket
from ..db import conditions
from ..image_utils import collect_images, collect_thumbnails, convert
from sqlalchemy.orm import sessionmaker
from sqlalchemy import desc
import datetime
import json
import redis
from pathlib import Path

WEBCONNS = []

def get_connections():
    return WEBCONNS

class MainHandler(tornado.web.RequestHandler):

    @property
    def navbar(self):
        return [
                ("Home", "/cassini/"),
                ("Gallery", "/cassini/history.html"),
                ("Gifs", "/cassini/gifs.html")

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
                    isActive="Home", 
                    navbar=self.navbar
                )

        if isMobile:
            self.render(
                    'mobile/index.html', 
                    **pageinfo
               )

        else:
            self.render(
                    'index.html',
                    **pageinfo
               )


class HistoryHandler(MainHandler):

    # @tornado.web.authenticated
    def get(self): 

        isMobile=False
        if "Mobile" in self.request.headers['User-Agent']:
            isMobile = True

        if isMobile:
            self.render('mobile/history.html', isActive="Gallery", navbar=self.navbar)
        else:
            self.render('history.html', isActive="Gallery", navbar=self.navbar)



class GifHandler(MainHandler):

    # @tornado.web.authenticated
    def get(self):
        gifpath = Path("/mnt/turtle/cache/gifs")

        self.render('gifs.html', isActive="GIFs", navbar=self.navbar, gifs=gifpath.iterdir())

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
        self.write_message(data)
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

