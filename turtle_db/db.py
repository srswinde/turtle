

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Float
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import datetime


engine = create_engine("mysql+pymysql://scott:4_sw@hle_4@192.168.0.148/turtle")
Base = declarative_base(bind=engine)


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


	

	

