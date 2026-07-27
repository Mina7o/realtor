import pymongo
import os
from otel_utils import init_otel

MONGO_URI = os.environ.get('MDB_CONNECTION_STRING', 'mongodb://localhost:27017')

_client = None
_tracer = None

def get_mongo():
    global _client, _tracer
    if _tracer is None:
        _tracer = init_otel("realtor-mongo")
    if _client is None:
        _client = pymongo.MongoClient(MONGO_URI)
    return _client['realtor_sovereign']
