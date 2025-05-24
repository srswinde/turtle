from tornado.web import RequestHandler
from tornado.web import StaticFileHandler
from pathlib import Path
from ..db import temp_sensors, mksession
import pandas as pd
import json
from tornado.escape import json_encode
import datetime
from dateutil.parser import parse

import logging
logging.basicConfig(level=logging.INFO)


class TempAnalysisHandler(RequestHandler):
    
    def get(self):
        self.render("temperatures.html")
        
    def post(self):
        
        try:
            data = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON {self.request.body}")
            data = {'timestamp': datetime.datetime.now().isoformat()}
            
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
        query = session.query(temp_sensors)\
            .filter(temp_sensors.timestamp > midnight.timestamp())\
            .filter(temp_sensors.timestamp < next_midnight.timestamp())
            
        df = pd.read_sql(query.statement, query.session.bind)
        df = df.groupby('address').apply(lambda x:x)
        addrs = df.index.get_level_values(0).unique()
        output = dict()
        mp = self.settings['temp_map']
        for addr in addrs:
            if addr in mp:
                
                output[mp[addr]] = df.loc[addr].to_dict()
        self.write(output)