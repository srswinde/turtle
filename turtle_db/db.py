

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


engine = create_engine("mysql+pymysql://scott:scott@192.168.0.148/turtle")
Base = declarative_base(bind=engine)
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


def build_imagedb():
    root_dir = Path('/mnt/turtle/imgs/2023')
    session = mksession()
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


def update_image(timestamp, hasTurtle):
    session = mksession()
    row = session.query(images).filter(images.timestamp == timestamp).first()
    row.hasTurtle = hasTurtle
    session.commit()
    session.close()


def mksession():

    session = sessionmaker(bind=conditions.metadata.bind)()
    return session


def log_conditions(data):

    session = sessionmaker(bind=conditions.metadata.bind)()
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
