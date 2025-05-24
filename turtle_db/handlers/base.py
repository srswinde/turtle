# -*- coding: utf-8 -*-

import tornado.web
import tornado.websocket
from ..image_utils import collect_images, collect_thumbnails, convert
import datetime
import json
import redis
from pathlib import Path
import os
import base64



WEBCONNS = []


def get_connections():
    return WEBCONNS

class BaseHandler(tornado.web.RequestHandler):
    
    def prepare(self):

        auth_header = self.request.headers.get('Authorization', None)

        if auth_header is None or not auth_header.startswith('Basic '):
            self.set_header('WWW-Authenticate', 'Basic realm="Restricted"')
            #self.redirect("/cassini/login")
            return
            #raise tornado.web.HTTPError(401)


        # Decode the credentials
        auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
        username, password = auth_decoded.split(':', 1)

        if username == "scott" and password == "scottandjosieforever":
            pass
        else:
            self.set_header('WWW-Authenticate', 'Basic realm="Restricted"')
            raise tornado.web.HTTPError(401)

    def get_current_user(self):
        return self.get_signed_cookie("user")

class IndexHandler(BaseHandler):
    
    def get(self):
        self.render('index.html')


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








class LoginHandler(tornado.web.RequestHandler):
    def get(self):
        self.write('<html><body><form action="/cassini/login" method="post">'
                   'Name: <input type="text" name="name">'
                   'Password: <input type="password" name="password">'
                   '<input type="submit" value="Sign in">'
                   '</form></body></html>')

    def post(self):
        user = self.get_argument("name")
        password = self.get_argument("password")
        if user == "scott" and password == "scottandjosieforever":
            
            self.set_signed_cookie("user", self.get_argument("name"), expires_days=1)
            self.redirect("/cassini/shed-cam")
        else:
            self.set_header('WWW-Authenticate', 'Basic realm="Restricted"')
            raise tornado.web.HTTPError(401)


class ImageHandler(tornado.web.StaticFileHandler):
    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.dirname = "/mnt/turtle/imgs/"

    def get_absolute_path(self, root, path):
        return os.path.join(self.dirname, path)



class BasicAuthMixin:
    def prepare(self):
        auth_header = self.request.headers.get('Authorization')
        if auth_header is None or not auth_header.startswith('Basic '):
            self._request_authentication()
        else:
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            if not self.check_credentials(username, password):
                self._request_authentication()

    def check_credentials(self, username, password):
        # Replace with your username and password validation logic
        return username == 'scott' and password == 'scottandjosieforever'

    def _request_authentication(self):
        self.set_header('WWW-Authenticate', 'Basic realm=Restricted')
        self.set_status(401)
        self.finish()
