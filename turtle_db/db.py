

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Float, String, Enum, insert, desc, and_
from sqlalchemy import create_engine
from sqlalchemy.sql import func
from sqlalchemy.orm import sessionmaker
import enum
from pathlib import Path
from pymysql.err import IntegrityError
import pandas as pd
import datetime
import requests
import re

Base = declarative_base()
TEST_CASE = True

class conditions(Base):

    __tablename__="conditions"

    timestamp=Column(Integer, primary_key=True)
    temperature_F=Column(Float)
    relative_humidity=Column(Float)

    def to_dict(self):
        return {
                'timestamp':self.timestamp,
                "temperature_F":self.temperature_F,
                "relative_humidity":self.relative_humidity
                }


class HAS_TURTLE(enum.Enum):
    YES = 1
    NO = 0
    NULL = -1

class images(Base):
    
    __tablename__="images"
    timestamp=Column(Integer, primary_key=True)
    path=Column(String(255))
    hasTurtle=Column(Enum(HAS_TURTLE), default=HAS_TURTLE.NULL)
    
class probabilities(Base):
    
    __tablename__="probabilities"
    timestamp=Column(Integer, primary_key=True)
    prob=Column(Float)

class pretrained(Base):
    __tablename__="pretrained"
    timestamp=Column(Integer, primary_key=True)
    model_file=Column(String(255), default="")
    prob=Column(Float)

class detections(Base):
    
    __tablename__="detections"
    timestamp=Column(Integer, primary_key=True)
    hasTurtle=Column(Enum(HAS_TURTLE), default=HAS_TURTLE.NULL)

class pretrained_models(Base):
    __tablename__="pretrained_models"
    id=Column(Integer, primary_key=True, autoincrement=True)
    timestamp=Column(Integer)
    model_file=Column(String(255), default="")
    prob=Column(Float)

class pretrained_20240311_0221(Base):
    __tablename__="pretrained_20240311_0221"
    timestamp=Column(Integer, primary_key=True)
    model_file=Column(String(255), default="")
    prob=Column(Float)

class temp_sensors(Base):
    
    __tablename__="temp_sensors"
    timestamp = Column(Integer, primary_key=True)
    address = Column(String(255))
    temp = Column(Float)
    
    

def log_temp_sensors(ip):
    rq = requests.get(f"http://{ip}/temps")
    
    data=rq.json()
    session = mksession()   
    for ii,(addr, temp) in enumerate(data.items()):
        
        row = temp_sensors()
        row.timestamp = datetime.datetime.now().timestamp()+ii
        row.address = addr
        row.temp = temp
        session.add(row)
    session.commit()
    session.close()
    
    return data
      

def recreate():
    md = Base.metadata
    s = mksession()
    md.bind = s.bind
    md.create_all(s.bind)
    s.close()



def build_imagedb():
    root_dir = Path('/mnt/turtle/imgs/2023')
    session = _model(bind=engine)
    rows = []
    
    first = session.query(images).order_by(desc(images.timestamp)).first()
    last_image = first.timestamp if first else 0
        
    
    
    for ii, img in enumerate(root_dir.glob('**/*.jpg')):
        if int(img.stem) <= last_image:
            continue
        try:
            ts = int(img.stem)
        except ValueError:
            continue

        row = dict(timestamp=ts, path=str(img), hasTurtle=HAS_TURTLE.NULL)
        rows.append(row)
        if ii % 100 == 0:
            print(ii)
            with engine.connect() as conn:
                conn.execute(insert(images).values(rows).prefix_with("IGNORE"))
            rows = []



class underground_temp(Base):
    
    __tablename__="underground_temp"
    timestamp=Column(Integer, primary_key=True)
    temp=Column(Float)

def get_rand_images(num=20):

    session = mksession()
    qry = session.query(images)\
        .filter(images.hasTurtle == HAS_TURTLE.NULL)\
            .filter(images.timestamp > 1685602800)\
                .filter(and_(
                    func.extract('hour', func.from_unixtime(images.timestamp)) > 15,
                    func.extract('hour', func.from_unixtime(images.timestamp)) < 24)
                    ).order_by(images.timestamp).limit(num)
    rows = pd.read_sql(qry.statement, qry.session.bind)
    rows.index = pd.to_datetime(rows.timestamp, unit='s')

    return rows

def get_prob_images(low, high, num=50, null=True, since=None):
    session = mksession()
    if since is None:
        since = datetime.datetime(2023, 7, 1, 0, 0, 0).timestamp()
        
    qry = session.query(pretrained_20240311_0221.timestamp, images.path, images.hasTurtle, pretrained_20240311_0221.prob)\
        .filter(pretrained_20240311_0221.timestamp > since)\
        .join(images, pretrained_20240311_0221.timestamp == images.timestamp)\
        .filter(pretrained_20240311_0221.prob > low)\
        .filter(pretrained_20240311_0221.prob < high)
    


    if null:
        qry = qry.filter(images.hasTurtle == HAS_TURTLE.NULL)

    qry=qry.limit(num)    
    df = pd.read_sql(qry.statement, qry.session.bind)
    df.index = pd.to_datetime(df.timestamp, unit='s')
    
    return df
    
                        
    

def update_image(timestamp, hasTurtle):
    session = mksession()
    row = session.query(images).filter(images.timestamp == timestamp).first()
    row.hasTurtle = hasTurtle
    session.commit()
    session.close()


def mksession():

    engine = create_engine("mysql+pymysql://scott:scott@localhost/turtle")
    session = sessionmaker(bind=engine)()
    return session


def log_conditions(data):

    #session = sessionmaker(bind=conditions.metadata.bind)()
    session = mksession()
    row = conditions()
    row.timestamp = data['timestamp']
    row.temperature_F = data['temp']
    row.relative_humidity = data['humid']
    session.add(row)
    session.commit()
    session.close()


def delete_row(timestamp):
    
    session = sessionmaker(bind=conditions.metadata.bind)()
    row=session.query(conditions).filter(
            conditions.timestamp == timestamp).first()
    session.delete(row)
    session.commit()


def get_row(timestamp):
    session = sessionmaker(bind=conditions.metadata.bind)()
    row=session.query(conditions).filter(
            conditions.timestamp == timestamp).first()

    return row.to_dict()


def gather_classified_images():
    session = mksession()
    qry = session.query(images).filter(images.hasTurtle != HAS_TURTLE.NULL)
    rows = pd.read_sql(qry.statement, qry.session.bind)
    rows['url'] = rows.path.apply(lambda x: x.replace('/mnt/turtle', 'staticturtle'))

    return rows

def detect_intervals(date=None, grouping_time=300, minprob=0.55):
    if date is None:
        date = datetime.datetime.now()
    prob_table = pretrained_20240311_0221
    
    start = datetime.datetime(date.year, date.month, date.day, 0, 0, 0)
    end = datetime.datetime(date.year, date.month, date.day, 23, 59, 59)
    
    session = mksession()
    qry = session.query(images, prob_table.prob)\
        .join(prob_table, images.timestamp == prob_table.timestamp)\
        .filter(images.timestamp > start.timestamp())\
        .filter(images.timestamp < end.timestamp())
        
    df = pd.read_sql(qry.statement, qry.session.bind)
    df['url'] = df['path']\
        .apply(lambda x: re.sub("\/mnt\/(turtle|nfs)\/imgs", "http://192.168.0.167:8000/cassini/static/staticturtle", x))
        
    prob = df.prob
    prob.index = pd.to_datetime(df.timestamp, unit='s')
    df = prob[prob > minprob]
    diff_series = df.index.to_series().diff().dt.total_seconds() > grouping_time
    group_series = diff_series.cumsum()
    grouped = df.groupby(group_series)
    grouped = grouped.apply(lambda x:x)
    
    if len(grouped) == 0:
        return grouped
    grouped.index.set_names('group', level=0, inplace=True)
    return grouped


def time_group(second_separator=300):
    
    session = mksession()
    minprob = 0.97
    hoursAgo = 24
    before = datetime.datetime.now() - datetime.timedelta(hours=hoursAgo)
    qry = session.query(func.from_unixtime(images.timestamp), images.path, images.hasTurtle, probabilities.prob)\
        .join(images, images.timestamp == probabilities.timestamp)\
        .filter(probabilities.prob > minprob)\
        .filter(images.timestamp > before.timestamp())\
        .filter(images.hasTurtle != HAS_TURTLE.NO)
        
    df = pd.read_sql(qry.statement, qry.session.bind)
    df.index = df.from_unixtime_1
    
    lowbin = pd.Interval(pd.Timedelta(seconds=0), pd.Timedelta(seconds=second_separator))
    higbin = pd.Interval(pd.Timedelta(seconds=second_separator), pd.Timedelta(seconds=second_separator*2))
    bins = pd.IntervalIndex([lowbin, higbin])
    df['bin'] = pd.cut(df.from_unixtime_1.diff(), bins)
    
    return df
