#!/usr/bin/env python3
"""Real OBS (throw-away config, obs-websocket): a "Video Capture Device (PipeWire)" source switched between the MXO2's
Composite and HDMI cameras by changing its device, as in the source properties; screenshots of the source after each
step (frame received or not) and the PipeWire stream states from the OBS log.
Usage: python3 -I obs_pw_switch_test.py WORK
"""
import base64
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obs_ws_test import start_obs, stop_obs  # noqa: E402

COMP, HDMI = 'v4l2_input.mxo2-mini-composite', 'v4l2_input.mxo2-mini-hdmi'


def main():
    work = os.path.abspath(sys.argv[1])
    u = str(uuid.uuid4())
    src = {'id': 'pipewire-camera-source', 'versioned_id': 'pipewire-camera-source', 'name': 'PW', 'uuid': u,
           'enabled': True, 'settings': {'device_id': COMP}}
    item = {'name': 'PW', 'source_uuid': u, 'visible': True, 'id': 1, 'pos': {'x': 0, 'y': 0},
            'scale': {'x': 1, 'y': 1}, 'align': 5, 'bounds_type': 0, 'rot': 0}
    p, ws, cfg = start_obs(work, [src], [item])
    if not ws:
        print('RESULT: no websocket (OBS exit %s)' % p.poll())
        sys.exit(1)
    crashed = None

    def shot(tag):
        r = ws.request('GetSourceScreenshot', {'sourceName': 'PW', 'imageFormat': 'png', 'imageWidth': 320})
        data = r.get('responseData', {}).get('imageData', '')
        if data:
            png = base64.b64decode(data.split(',', 1)[1])
            path = os.path.join(work, 'shot-%s.png' % tag)
            open(path, 'wb').write(png)
            print('  screenshot %s: %d bytes -> %s' % (tag, len(png), path), flush=True)
        else:
            print('  screenshot %s: none (%s)' % (tag, r.get('requestStatus')), flush=True)

    try:
        time.sleep(4)
        r = ws.request('GetInputSettings', {'inputName': 'PW'})
        print('start settings:', r.get('responseData', {}).get('inputSettings'), flush=True)
        shot('1-composite')
        for tag, dev in (('2-hdmi', HDMI), ('3-composite', COMP)):
            r = ws.request('SetInputSettings', {'inputName': 'PW', 'inputSettings': {'device_id': dev}})
            print('switch to %s: %s' % (dev, 'ok' if r['requestStatus'].get('result') else r['requestStatus']),
                  flush=True)
            time.sleep(4)
            shot(tag)
    except (ConnectionError, OSError) as e:
        crashed = str(e)
    alive = stop_obs(p, cfg, keys=('camera-portal', 'pipewire]', 'rror', 'Size', 'Format', 'Framerate'))
    print('RESULT: %s' % ('OBS CRASHED (%s)' % crashed if crashed or not alive else 'OBS survived'))


if __name__ == '__main__':
    main()
