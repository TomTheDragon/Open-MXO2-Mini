#!/usr/bin/env python3
"""Drive a real OBS (throw-away config) over obs-websocket: add a "Video Capture Device (V4L2)" source the way the
"Add source" dialog does and request its property lists (device, input, resolution, framerate), optionally while a
PipeWire camera source streams the same card. Detects OBS crashing.
Usage: python3 -I obs_ws_test.py WORK [pipewire]
"""
import base64
import glob
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid

PORT = 4466


class WS:
    """Minimal websocket client (text frames, no extensions)."""

    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(('GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n'
                        'Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n' % (port, key)).encode())
        hdr = b''
        while b'\r\n\r\n' not in hdr:
            hdr += self.s.recv(1)
        assert b' 101 ' in hdr.split(b'\r\n')[0], hdr

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n):
        b = b''
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise ConnectionError('closed')
            b += c
        return b

    def recv(self):
        while True:
            h = self._exact(2)
            n = h[1] & 0x7f
            if n == 126:
                n = struct.unpack('>H', self._exact(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._exact(8))[0]
            data = self._exact(n)
            if h[0] & 0x0f == 1:
                return json.loads(data)

    def request(self, rtype, data=None):
        rid = str(uuid.uuid4())
        self.send({'op': 6, 'd': {'requestType': rtype, 'requestId': rid, 'requestData': data or {}}})
        while True:
            m = self.recv()
            if m.get('op') == 7 and m['d'].get('requestId') == rid:
                return m['d']


def start_obs(work, sources=(), items=()):
    """Throw-away OBS config (websocket on, scene 'Scene' with the given sources/items), start OBS, connect.
    Returns (process, websocket, config dir)."""
    cfg = os.path.join(work, 'config', 'obs-studio')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(cfg, 'basic', 'profiles', 'wstest'))
    os.makedirs(os.path.join(cfg, 'basic', 'scenes'))
    os.makedirs(os.path.join(cfg, 'plugin_config', 'obs-websocket'))
    json.dump({'server_enabled': True, 'server_port': PORT, 'auth_required': False, 'alerts_enabled': False,
               'first_load': False}, open(os.path.join(cfg, 'plugin_config', 'obs-websocket', 'config.json'), 'w'))
    user_global = os.path.expanduser('~/.config/obs-studio/global.ini')
    g = open(user_global).read() if os.path.exists(user_global) else '[General]\n'
    g = g.replace('ConfirmOnExit=true', 'ConfirmOnExit=false')
    g += ('\n[Basic]\nProfile=wstest\nProfileDir=wstest\nSceneCollection=wstest\nSceneCollectionFile=wstest\n'
          '\n[OBSWebSocket]\nServerEnabled=true\nServerPort=%d\nAuthRequired=false\nAlertsEnabled=false\n'
          'FirstLoad=false\n' % PORT)
    open(os.path.join(cfg, 'global.ini'), 'w').write(g)
    open(os.path.join(cfg, 'basic', 'profiles', 'wstest', 'basic.ini'), 'w').write(
        '[General]\nName=wstest\n\n[Video]\nBaseCX=1280\nBaseCY=720\nOutputCX=1280\nOutputCY=720\nFPSType=0\n'
        'FPSCommon=60\n')
    srcs = [{'id': 'scene', 'versioned_id': 'scene', 'name': 'Scene', 'uuid': str(uuid.uuid4()), 'enabled': True,
             'settings': {'id_counter': len(items), 'items': list(items)}}] + list(sources)
    json.dump({'name': 'wstest', 'current_scene': 'Scene', 'current_program_scene': 'Scene',
               'scene_order': [{'name': 'Scene'}], 'sources': srcs, 'groups': [], 'transitions': []},
              open(os.path.join(cfg, 'basic', 'scenes', 'wstest.json'), 'w'))
    env = dict(os.environ, XDG_CONFIG_HOME=os.path.join(work, 'config'))
    env.setdefault('DISPLAY', ':0')
    p = subprocess.Popen(['obs', '--collection', 'wstest', '--profile', 'wstest', '--minimize-to-tray',
                          '--disable-shutdown-check', '--disable-missing-files-check'],
                         env=env, stdout=open(os.path.join(work, 'stdout.txt'), 'w'), stderr=subprocess.STDOUT,
                         cwd=work)
    ws = None
    for _ in range(60):
        time.sleep(0.5)
        try:
            ws = WS(PORT)
            break
        except OSError:
            if p.poll() is not None:
                break
    if ws:
        ws.recv()
        ws.send({'op': 1, 'd': {'rpcVersion': 1}})
        ws.recv()
    return p, ws, cfg


def stop_obs(p, cfg, keys=('v4l2', 'camera-portal', 'pipewire]', 'rror', 'crash', 'Crash')):
    """Stop OBS if still running; print matching log lines; True if it was still alive."""
    alive = p.poll() is None
    if alive:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
    logs = sorted(glob.glob(os.path.join(cfg, 'logs', '*.txt')))
    if logs:
        for line in open(logs[-1], errors='replace').read().splitlines()[-500:]:
            if any(k in line for k in keys):
                print('  log: ' + line)
    return alive


def main():
    work = os.path.abspath(sys.argv[1])
    with_pw = len(sys.argv) > 2 and sys.argv[2] == 'pipewire'
    items, sources = [], []
    if with_pw:
        u = str(uuid.uuid4())
        sources.append({'id': 'pipewire-camera-source', 'versioned_id': 'pipewire-camera-source', 'name': 'PW',
                        'uuid': u, 'enabled': True, 'settings': {'device_id': 'v4l2_input.mxo2-mini-composite'}})
        items.append({'name': 'PW', 'source_uuid': u, 'visible': True, 'id': 1, 'pos': {'x': 0, 'y': 0},
                      'scale': {'x': 1, 'y': 1}, 'align': 5, 'bounds_type': 0, 'rot': 0})
    p, ws, cfg = start_obs(work, sources, items)
    if not ws:
        print('RESULT: could not connect to obs-websocket (OBS exit %s)' % p.poll())
        sys.exit(1)
    time.sleep(3)
    crashed = None
    try:
        steps = [('CreateInput', {'sceneName': 'Scene', 'inputName': 'V4L2', 'inputKind': 'v4l2_input',
                                  'inputSettings': {}}),
                 ('GetInputPropertiesListPropertyItems', {'inputName': 'V4L2', 'propertyName': 'device_id'}),
                 ('SetInputSettings', {'inputName': 'V4L2', 'inputSettings': {'device_id': '/dev/video0'}}),
                 ('GetInputPropertiesListPropertyItems', {'inputName': 'V4L2', 'propertyName': 'input'}),
                 ('GetInputPropertiesListPropertyItems', {'inputName': 'V4L2', 'propertyName': 'resolution'}),
                 ('GetInputPropertiesListPropertyItems', {'inputName': 'V4L2', 'propertyName': 'framerate'}),
                 ('GetInputSettings', {'inputName': 'V4L2'})]
        for rtype, data in steps:
            r = ws.request(rtype, data)
            st = r.get('requestStatus', {})
            out = r.get('responseData', {})
            if 'propertyItems' in out:
                out = [i.get('itemName') for i in out['propertyItems']]
            print('%-36s %s %s' % (rtype + ' ' + data.get('propertyName', ''), 'ok' if st.get('result') else
                                     'FAILED %s' % st.get('comment'), json.dumps(out)[:200]), flush=True)
            time.sleep(1)
    except (ConnectionError, OSError) as e:
        crashed = str(e)
    time.sleep(1)
    alive = stop_obs(p, cfg)
    print('RESULT: %s' % ('OBS CRASHED (%s, exit %s)' % (crashed, p.returncode) if crashed or not alive else
                          'OBS survived'))


if __name__ == '__main__':
    main()
