# worker.py - Background job processor
import os
from redis import Redis
from rq import Worker, Queue, Connection

redis_url = os.environ.get('REDIS_URL')
redis_conn = Redis.from_url(redis_url, ssl_cert_reqs=None, decode_responses=True)

listen = ['default']

if __name__ == '__main__':
    with Connection(redis_conn):
        worker = Worker(list(map(Queue, listen)))
        print(f'[WORKER] Starting RQ worker, listening on: {listen}')
        worker.work()