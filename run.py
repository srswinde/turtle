#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Basic run script"""
import asyncio
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.autoreload
from tornado.options import options
import tornado.web
from settings import settings
from turtle_db.urls import url_patterns
from turtle_db.handlers.base import get_connections
import redis
from turtle_db import db
from turtle_db.db import conditions, log_conditions
from sqlalchemy import select, insert
from sqlalchemy.orm import sessionmaker
import json
import time
import datetime
from pathlib import Path
import aiohttp
from pyindi.webclient import INDIWebApp, INDIHandler
from turtle_db import db



class INDIMainPage(INDIHandler):

    def get(self):
        self.indi_render(Path.cwd()/"turtle_db/templates/picam2.html", devices=['*'], title="TITLE", example_variable="EXAMPLE")

def handle_blob(blob):
    dtnow = datetime.datetime.now()
    now=int(time.time())
    imname = f'{now}.jpg'
    remote_nowpic = Path('/mnt/nfs/imgs')
    remote_nowpic/= dtnow.strftime("%Y")
    remote_nowpic/= dtnow.strftime("%b")
    remote_nowpic/= dtnow.strftime("%d")
    remote_nowpic.mkdir(parents=True, exist_ok=True)
    remote_nowpic/=imname
    remote_nowpic = str(remote_nowpic)
    nowpic = Path('/mnt/nfs/imgs/latest/latest.jpg')


    with open(remote_nowpic, 'wb') as archive:
        archive.write(blob['data'])

    with open(nowpic, 'wb') as latest:
        latest.write(blob['data'])

    row = db.images(path=str(remote_nowpic), timestamp=now, hasTurtle=db.HAS_TURTLE.NULL)
    session = db.mksession()
    session.add(row)
    print(f"Adding row f{dtnow}")
    session.commit()
    
    rconn = redis.Redis(host="localhost")
    data = {
            "time": dtnow.ctime(),
            "name": remote_nowpic,
            "timestamp": dtnow.timestamp()
            }
    rconn.publish("home_image", json.dumps(data))
    rconn.set("home_image", json.dumps(data))



class TornadoApplication(tornado.web.Application):

    def __init__(self):
        iwa = INDIWebApp(indihost="192.168.0.205", indiport=7624, handle_blob=handle_blob)
        ihandlers = iwa.indi_handlers()
        url_patterns.extend(ihandlers)
        url_patterns.append((r"/indi/imain", INDIMainPage))
        print(url_patterns)
        
        tornado.web.Application.__init__(self, url_patterns, **settings)



async def start_redis():
    r = redis.Redis()
    p = r.pubsub()
    p.subscribe("turtle_conditions")
    p.subscribe("home_image")
    p.subscribe("backyard_image")
    p.subscribe("tanspot_image")
    loop = asyncio.get_running_loop()
    while 1:
        msg = await loop.run_in_executor( None, p.parse_response )
        clean_msg = p.handle_message(msg, True)
        if clean_msg:

            data = json.loads(msg[2].decode())
            if "temp" in data: # only log temp data not recent images
                loop.run_in_executor(None, log_conditions, data)


            connections = get_connections()
            print(connections)
            for ws in connections:
                if "temp" in data:
                    ws.write_message(json.dumps({"temp":data}))
                elif "name" in data:
                    print("sending image")
                    if "backyard" in data['name']:

                        data['name'] = data['name'].replace("/mnt/turtle", "staticturtle")
                        ws.write_message(json.dumps({"backyard_image":data}))
                    elif "tanspot" in data["name"]:
                        data['name'] = data['name'].replace("/mnt/turtle", "staticturtle")
                        ws.write_message(json.dumps({"tanspot_image":data}))
                    else:
                        data['name'] = data['name'].replace("/mnt/turtle", "staticturtle")
                        ws.write_message(json.dumps({"home_image":data}))


async def get_underground_temp():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://192.168.0.139/temp"
                async with session.get(url) as resp:
                    temp = await resp.read()
        except Exception as error:
            print(f"Error with underground temp: {error}")
            continue
        temp = temp.decode()
        r=redis.Redis()
        r.set("underground_temp", temp)
        r.publish("underground_temp", temp)
        
        print(f"temp is {temp}")
        row = db.underground_temp(timestamp=int(time.time()), temp=float(temp))
        session = db.mksession()
        session.add(row)
        session.commit()
        
        await asyncio.sleep(5.0)
        


async def grabTanspot():
    while 1:
        try:
            dtnow = datetime.datetime.now()
            now=int(time.time())
            remote_nowpic = Path('/mnt/turtle/imgs/tanspot')
            remote_nowpic/= dtnow.strftime("%Y")
            remote_nowpic/= dtnow.strftime("%b")
            remote_nowpic/= dtnow.strftime("%d")
            remote_nowpic.mkdir(parents=True, exist_ok=True)
            remote_nowpic/=f'{now}.jpg'
            remote_nowpic = str(remote_nowpic)
            nowpic = Path('/mnt/turtle/imgs/tanspot/latest.jpg')
        except Exception as error:
            print(error)

        try:
            # trying both possible IP addresses
            # laziest programming ever. 
#            async with aiohttp.ClientSession() as session:
#                url = f"http://192.168.0.166/capture?_cb={time.time()}"
#                async with session.get(url) as resp:
#                    image = await resp.read()
#                    
#                    with open(remote_nowpic, 'wb') as jpg:
#                        jpg.write(image)
#
#                    with open(nowpic, 'wb') as latest:
#                        latest.write(image)

            async with aiohttp.ClientSession() as session:
                url = f"http://192.168.0.166/capture?_cb={time.time()}"
                async with session.get(url) as resp:
                    image = await resp.read()
                    
                    with open(remote_nowpic, 'wb') as jpg:
                        jpg.write(image)

                    with open(nowpic, 'wb') as latest:
                        latest.write(image)

        except Exception as error:
            print(f"Error with tanspot image: {error}")
           
        try:
            rconn = redis.Redis( host="cabinet.local")
            data = {
                    "time": dtnow.ctime(),
                    "name": remote_nowpic,
                    "timestamp": dtnow.timestamp()
                    }

            rconn.publish("tanspot_image", json.dumps(data))
            rconn.set("tanspot_image", json.dumps(data))

            await asyncio.sleep(20.0)
        except Exception as error:
            print(f"redis error {error}")


def main():
    

    app = TornadoApplication()
    app.listen(options.port)
    print(options.port)
    loop = tornado.ioloop.IOLoop.current()
    loop.asyncio_loop.create_task(start_redis())
    #loop.asyncio_loop.create_task(get_underground_temp())
#    loop.asyncio_loop.create_task(grabTanspot())
    loop.start()




if __name__ == "__main__":
    main()
