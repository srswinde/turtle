from tornado.web import RequestHandler
from tornado.web import StaticFileHandler
from tornado.web import authenticated
from pathlib import Path
from .base import BaseHandler, BasicAuthMixin
from ..db import get_dataframe, shed_camera, mksession, HAS_TURTLE
import pandas as pd
import json
from tornado.escape import json_encode
import datetime
from dateutil.parser import parse

import logging
logging.basicConfig(level=logging.INFO)

class TurtleEncoder(json.JSONEncoder):
    def default(self, obj):
        if obj in [HAS_TURTLE.YES, HAS_TURTLE.NO, HAS_TURTLE.NULL]:
            # replace with your actual serialization logic
            return obj.value
        return super().default(obj)


class AuthenticatedStaticFileHandler(BasicAuthMixin, StaticFileHandler):
    pass

class ShedAnalysisHandler(BaseHandler):
    @authenticated
    def get(self):
        self.render("shed-cam.html")
    
    @authenticated
    def post(self):
        data = json.loads(self.request.body.decode('utf-8'))
        if 'timestamp' in data:
            try:
                timestamp = parse(data['timestamp'])
            except TypeError:
                timestamp = datetime.datetime.now()
                logging.error(f"Invalid timestamp {data['timestamp']}")
            
            
        else:
            timestamp = datetime.datetime.now()
        
        
        
        midnight = datetime.datetime(timestamp.year, timestamp.month, timestamp.day)
        next_midnight = midnight + datetime.timedelta(days=1)
        
        session = mksession()
        query = session.query(shed_camera)\
            .filter(shed_camera.timestamp > midnight.timestamp()*1000)\
            .filter(shed_camera.timestamp < next_midnight.timestamp()*1000)
        
        df = pd.read_sql(query.statement, query.session.bind)
        
        #regex to extract position from path
        regex = "snapshot_(\d{1,3})_(\d{1,3})_(\d{1,2})"
        path_with_position = df.path.str.contains(regex)
        
        df = df[path_with_position].copy()
        
        position = df.path.str.extractall(regex)
        position.index = df.index
        df['azimuth'] = position[0].astype(int)
        df['elevation'] = position[1].astype(int)
        df['count'] = position[2].astype(int)
        
        if len(df) == 0:
            logging.info(f"No data for {midnight}")
        df = df[df.hasTurtle == HAS_TURTLE.NULL].copy()
        df.index = df.timestamp.apply(lambda x: x)
        df= df[['path', 'prob', 'azimuth', 'elevation', 'count']]
        df.path = df.path.str.replace('/mnt/nfs/shed-cam/', '/cassini/shed-cam/static/')
        self.write(df.to_dict())
        
        
class UpdateShedDbHandler(BaseHandler):
    @authenticated
    def get(self):
        
        timestamp = int(self.get_argument('timestamp'))-60*1000
        logging.info(f"Getting data after {timestamp}")
        session = mksession()
        qry = session.query(shed_camera).filter(shed_camera.hasTurtle == HAS_TURTLE.NULL).filter(shed_camera.timestamp > timestamp).order_by(shed_camera.timestamp).limit(20)
        
        df = pd.read_sql(qry.statement, qry.session.bind)
        df['path'] = df.path.str.replace('/mnt/nfs/shed-cam/', '/cassini/shed-cam/static/')
        self.set_header("Content-Type", "application/json")
        json_str = json.dumps(df.to_dict(), cls=TurtleEncoder).replace("</", "<\\/")
        self.write(json_str)
        
        
        
    @authenticated
    def post(self):
        data = json.loads(self.request.body.decode('utf-8'))
        logging.error(data)
        if data['has_turtle'] == True:
            hasTurtle = HAS_TURTLE.YES
        elif data['has_turtle'] == False:
            hasTurtle = HAS_TURTLE.NO
        else:
            raise ValueError(f"Invalid value for hasTurtle {data['hasTurtle']}")
        
        # sanity check on timestamp (within 20 years of now)
        timestamp = data['timestamp']
        dt = datetime.datetime.fromtimestamp(timestamp/1000)
        if dt.year < 2000 or dt.year > 2040:
            raise ValueError(f"Invalid timestamp {dt}")
        
        if hasTurtle == HAS_TURTLE.YES:
            # sanity check on x and y
            x = float(data['x'])
            y = float(data['y'])
            
            if x < 0 or x > 1:
                raise ValueError(f"Invalid x {x}")
            if y < 0 or y > 1:
                raise ValueError(f"Invalid y {y}")
            
        session = mksession()
        row = session.query(shed_camera).filter(shed_camera.timestamp == timestamp).first()
        row.hasTurtle = hasTurtle
        if hasTurtle == HAS_TURTLE.YES:
            row.x = x
            row.y = y
        
            logging.info(f"Updating row with timestamp {timestamp} to {hasTurtle} {x} {y}")
        else:
            logging.info(f"Updating row with timestamp {timestamp} to {hasTurtle}")
        session.commit()
        
        
        #log_conditions(data)
        self.write('success')
