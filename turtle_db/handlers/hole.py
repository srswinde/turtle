from tornado.web import RequestHandler
from tornado.web import StaticFileHandler
from pathlib import Path
from ..db import get_dataframe, shed_camera, mksession, HAS_TURTLE
from ..db import hole_camera, images
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

        


class HoleAnalysisHandler(RequestHandler):

    def get(self):
        self.render("hole.html")
        
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
        query = session.query(hole_camera)\
            .filter(hole_camera.timestamp > midnight.timestamp()*1000)\
            .filter(hole_camera.timestamp < next_midnight.timestamp()*1000)
        
        df = pd.read_sql(query.statement, query.session.bind)
        
        logging.info(df.columns)
        logging.info(len(df))
        
        if len(df) == 0:
            logging.info(f"No data for {midnight}")
        df = df[df.hasTurtle == HAS_TURTLE.NULL].copy()
        df.index = df.timestamp.apply(lambda x: x)
        df.path = df.path.str.replace('/mnt/nfs/hole-cam/', '/cassini/hole-cam/static/')
        df = df[['path', 'prob']]
        self.write(df.to_dict())
        
        
class UpdateHoleDbHandler(RequestHandler):
    
    def get(self):
        
        timestamp = int(self.get_argument('timestamp'))-60*1000
        logging.info(f"Getting data after {timestamp}")
        session = mksession()
        qry = session.query(hole_camera)\
            .filter(hole_camera.hasTurtle == HAS_TURTLE.NULL)\
            .filter(hole_camera.timestamp > timestamp)\
            .order_by(hole_camera.timestamp)\
            .limit(20)
        
        df = pd.read_sql(qry.statement, qry.session.bind)
        df['path'] = df.path.str.replace('/mnt/nfs/hole-cam/', '/cassini/hole-cam/static/')
        self.set_header("Content-Type", "application/json")
        json_str = json.dumps(df.to_dict(), cls=TurtleEncoder).replace("</", "<\\/")
        self.write(json_str)
        
    
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
        row = session.query(hole_camera)\
            .filter(hole_camera.timestamp == timestamp).first()
        row.hasTurtle = hasTurtle
        if hasTurtle == HAS_TURTLE.YES:
        
            logging.info(f"Updating row with timestamp {timestamp} to {hasTurtle} {x} {y}")
        else:
            logging.info(f"Updating row with timestamp {timestamp} to {hasTurtle}")
        session.commit()
        
        
        #log_conditions(data)
        self.write('success')