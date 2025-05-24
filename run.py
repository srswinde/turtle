#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Basic run script"""
import asyncio
import tornado.httpserver
import tornado.ioloop
import tornado.web
import os
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






class TornadoApplication(tornado.web.Application):

    def __init__(self):
        
        os.environ["TURTLE_DB_URI"] = settings["TURTLE_DB_URI"]
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
                url = f"http://192.168.1.157/temps"
                async with session.get(url) as resp:
                    temp = await resp.json()
        except Exception as error:
            print(f"Error with underground temp: {error}")
            continue
        #temp = temp.decode()
        #r=redis.Redis()
        #r.set("underground_temp", temp)
        #r.publish("underground_temp", temp)
        
        session = db.mksession()
        for ii, (addr, temp) in enumerate(temp.items()):
            row = db.temp_sensors(timestamp=int(time.time()+ii), address=addr, temp=float(temp))
            
            session.add(row)
            
        session.commit()
        
        await asyncio.sleep(5.0)
        





def main():
    

    app = TornadoApplication()
    app.listen(options.port)
    print(options.port)
    loop = tornado.ioloop.IOLoop.current()
    loop.asyncio_loop.create_task(start_redis())
    loop.asyncio_loop.create_task(get_underground_temp())
    loop.start()




if __name__ == "__main__":
    main()
