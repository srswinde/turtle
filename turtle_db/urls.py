# -*- coding: utf-8 -*-

from .handlers import base
from tornado.web import StaticFileHandler
from .handlers import shed
from .handlers import hole
from .handlers import temperatures
from .handlers import db_passthrough
from .handlers import old_base


url_patterns = [
    (r'/cassini/', base.IndexHandler),
    (r'/cassini/websocket', base.Websocket),
    (r'/cassini/images', base.ImageSocket),
    (r'/cassini/image_files/(.*)', base.ImageHandler, {'path': '/'}),
    
    # Old base
    (r'/cassini/history.html', old_base.HistoryHandler),
    (r'/cassini/gifs.html', old_base.GifHandler),
    (r'/cassini/db/temp/(.*)', old_base.DataTemperatures),
    (r'/cassini/db/images/(.*)', old_base.DataImages),
    (r'/cassini/recent_detect', old_base.RecentDetectHandler),
    (r'/cassini/detect_intervals', old_base.CassiniIntervals),
    (r'/cassini/lights.html', old_base.Lights),
    (r'/cassini/detect', old_base.DetectHandler),
    (r'/cassini/edge_cases', old_base.EdgeCaseHandler),

    # Static file handlers
    (r'/cassini/hole-cam/static/(.*)', StaticFileHandler, {'path': '/mnt/nfs/hole-cam/'}),
    #(r'/cassini/staticturtle/(.*)', StaticFileHandler, {'path': '/mnt/nfs/'}),
    (r'/cassini/shed-cam/static/(.*)', shed.AuthenticatedStaticFileHandler, {'path': '/mnt/nfs/shed-cam/'}),
    # Shed handlers
    (r'/cassini/shed-cam', shed.ShedAnalysisHandler),
    (r'/cassini/shed-cam/UpdateDb', shed.UpdateShedDbHandler),
    (r'/cassini/shed-cam/resnet', shed.ResnetHandler),
    
    #hole handlers
    (r'/cassini/hole-cam', hole.HoleMainHandler),
    (r'/cassini/hole-cam/UpdateDb', hole.UpdateHoleDbHandler),
    (r'/cassini/detections.html', hole.HoleAnalysisHandler),
    (r'/cassini/hole-cam/resnet', hole.HoleResnetHandler),
    (r'/cassini/hole-cam/resnet/train', hole.HoleResnetTrainHandler),
    (r'/cassini/hole-cam/resnet/train/update', hole.HoleResnetTrainUpdateHandler),
    
    
    #temp handlers
    (r'/cassini/temperatures.html', temperatures.TempAnalysisHandler),
    
    (r'/cassini/login', base.LoginHandler),
    (r'/cassini/index-new.html', base.IndexHandler),
    
    #db passthrough
    (r'/cassini/db/(.*)/(.*)', db_passthrough.GetTableHandler),
    
]
