# -*- coding: utf-8 -*-

from .handlers import base
from tornado.web import StaticFileHandler
from .handlers import shed
from .handlers import hole


url_patterns = [
    (r'/cassini/', base.MainHandler),
    (r'/cassini/history.html', base.HistoryHandler),
    (r'/cassini/gifs.html', base.GifHandler),
    (r'/cassini/websocket', base.Websocket),
    (r'/cassini/images', base.ImageSocket),
    (r'/cassini/db/temp/(.*)', base.DataTemperatures),
    (r'/cassini/db/images/(.*)', base.DataImages),
    (r'/cassini/playground', base.ImagesPlayground),
    (r'/cassini/lights.html', base.Lights),
    (r'/cassini/image_files/(.*)', base.ImageHandler, {'path': '/'}),
    (r'/cassini/detect', base.DetectHandler),
    (r'/cassini/recent_detect', base.RecentDetectHandler),
    (r'/cassini/detect_intervals', base.CassiniIntervals),
    (r'/cassini/edge_cases', base.EdgeCaseHandler),
    (r'/cassini/playground/', base.ImagesPlayground),
    # Static file handlers
    (r'/cassini/hole-cam/static/(.*)', StaticFileHandler, {'path': '/mnt/nfs/imgs/'}),
    (r'/cassini/staticturtle/(.*)', StaticFileHandler, {'path': '/mnt/nfs/'}),
    (r'/cassini/shed-cam/static/(.*)', StaticFileHandler, {'path': '/mnt/nfs/shed-cam/'}),
    # Shed handlers
    (r'/cassini/shed-cam', shed.ShedAnalysisHandler),
    (r'/cassini/shed-cam/UpdateDb', shed.UpdateShedDbHandler),
    #hole handlers
    (r'/cassini/hole-cam', hole.HoleAnalysisHandler),
    (r'/cassini/hole-cam/UpdateDb', hole.UpdateHoleDbHandler)
]
