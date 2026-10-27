"""Board deployments opt into LAN administration restrictions; Pixel defaults stay intact."""
import os,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from board_transport import install
for restricted in ['0','1']:
    with patch.dict(os.environ,{'OMNIBOT_BOARD_LOCAL_ADMIN':restricted}):
        app=FastAPI()
        @app.get('/ping')
        def ping():return {'status':'ready'}
        install(app,SimpleNamespace())
        with TestClient(app,client=('192.0.2.1',1234)) as client:
            assert client.get('/ping').status_code==(403 if restricted=='1' else 200)
        with TestClient(app,client=('127.0.0.1',1234)) as client:
            assert client.get('/ping').status_code==200
print('PASS: default LAN behavior preserved; opt-in restriction blocks remote administration and permits loopback')
