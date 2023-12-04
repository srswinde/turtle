# -*- coding: utf-8 -*-

from .handlers import base
from tornado.web import StaticFileHandler


url_patterns = [
    (r'/cassini/', base.MainHandler),
    (r'/cassini/history.html', base.HistoryHandler),
    (r'/cassini/gifs.html', base.GifHandler),
    (r'/cassini/websocket', base.Websocket),
    (r'/cassini/images', base.ImageSocket),
    (r'/cassini/db/temp/(.*)', base.DataTemperatures),
    (r'/cassini/db/images/(.*)', base.DataImages),
    (r'/cassini/test', base.Test),
    (r'/cassini/lights.html', base.Lights),
    (r'/cassini/image_files/(.*)', base.ImageHandler, {'path': '/'}),
    (r'/cassini/detect', base.DetectHandler),
    (r'/cassini/recent_detect', base.RecentDetectHandler),
    (r'/cassini/detect_intervals', base.CassiniIntervals),
]
