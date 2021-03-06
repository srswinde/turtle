# -*- coding: utf-8 -*-

from .handlers import base
from tornado.web import StaticFileHandler


url_patterns = [
    (r'/cassini/', base.MainHandler),
    (r'/cassini/history.html', base.HistoryHandler),
    (r'/cassini/gifs.html', base.GifHandler),
    (r'/cassini/websocket', base.Websocket),
    (r'/cassini/images', base.ImageSocket),
    (r'/cassini/db/(.*)', base.Data),
    (r'/cassini/test', base.Test)
]
