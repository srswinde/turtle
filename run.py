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
from turtle_db.db import conditions, log_conditions
from sqlalchemy import select, insert
from sqlalchemy.orm import sessionmaker
import json
import time

class TornadoApplication(tornado.web.Application):

    def __init__(self):
        tornado.web.Application.__init__(self, url_patterns, **settings)



async def start_redis():
    r = redis.Redis()
    p = r.pubsub()
    p.subscribe("turtle_conditions")
    loop = asyncio.get_running_loop()
    while 1:
        msg = await loop.run_in_executor( None, p.parse_response )
        clean_msg = p.handle_message(msg, True)
        if clean_msg:
            data = json.loads(msg[2].decode())
            loop.run_in_executor(None, log_conditions, data)
            for ws in get_connections():
                print(msg[2])
                ws.write_message(msg[2])





def main():
    

    app = TornadoApplication()
    app.listen(options.port)
    loop = tornado.ioloop.IOLoop.current()
    loop.asyncio_loop.create_task(start_redis())
    loop.start()




if __name__ == "__main__":
    main()
