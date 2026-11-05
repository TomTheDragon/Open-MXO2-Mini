#!/usr/bin/env python3
"""Record from the MXO2 with the real OBS Studio, using a throw-away config (the user's OBS setup is untouched).

Builds $WORK/config/obs-studio with one profile (simple output, MKV, 1280x720 @ 60) and one scene collection holding
a "Video Capture Device (V4L2)" source (device, input, resolution as given) and an ALSA source on the MXO2, starts
OBS minimised with --startrecording, stops it after SECS, then checks the recording with ffprobe/ffmpeg and prints
the OBS log lines about v4l2/alsa.
Usage: python3 -I obs_live_test.py WORK [input 0 Auto|1 HDMI|2 Composite] [width height] [secs]
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid

WORK = os.path.abspath(sys.argv[1])
INPUT = int(sys.argv[2]) if len(sys.argv) > 2 else 0       # V4L2 input: 0 Auto, 1 HDMI, 2 Composite
W, H = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (0, 0)
SECS = float(sys.argv[5]) if len(sys.argv) > 5 else 12
YUYV = int.from_bytes(b'YUYV', 'little')

cfg = os.path.join(WORK, 'config', 'obs-studio')
rec = os.path.join(WORK, 'rec')
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(os.path.join(cfg, 'basic', 'profiles', 'mxo2test'))
os.makedirs(os.path.join(cfg, 'basic', 'scenes'))
os.makedirs(rec)
user_global = os.path.expanduser('~/.config/obs-studio/global.ini')
glob_ini = open(user_global).read() if os.path.exists(user_global) else '[General]\n'
glob_ini = glob_ini.replace('ConfirmOnExit=true', 'ConfirmOnExit=false')
glob_ini += '\n[Basic]\nProfile=mxo2test\nProfileDir=mxo2test\nSceneCollection=mxo2test\nSceneCollectionFile=mxo2test\n'
open(os.path.join(cfg, 'global.ini'), 'w').write(glob_ini)
open(os.path.join(cfg, 'basic', 'profiles', 'mxo2test', 'basic.ini'), 'w').write(f"""[General]
Name=mxo2test

[Video]
BaseCX=1280
BaseCY=720
OutputCX=1280
OutputCY=720
FPSType=0
FPSCommon=60

[Audio]
SampleRate=48000
ChannelSetup=Stereo

[Output]
Mode=Simple

[SimpleOutput]
FilePath={rec}
RecFormat2=mkv
RecQuality=Small
RecEncoder=x264
StreamEncoder=x264
VBitrate=2500
""")
scene_uuid, v_uuid, a_uuid = (str(uuid.uuid4()) for _ in range(3))
v_settings = {'device_id': '/dev/video0', 'input': INPUT, 'pixelformat': YUYV, 'buffering': False}
if W:
    v_settings['resolution'] = (W << 16) | H
# MXO2_SRC=pipewire: OBS' "Video Capture Device (PipeWire)" (camera portal) instead of the V4L2 source
if os.environ.get('MXO2_SRC') == 'pipewire':
    V_SOURCE = {'id': 'pipewire-camera-source', 'versioned_id': 'pipewire-camera-source', 'name': 'MXO2',
                'uuid': v_uuid, 'enabled': True, 'settings': {'device_id': 'v4l2_input.pci-0000_02_00.0', 'pixelformat': 4,
                             'resolution': json.dumps({'width': W or 720, 'height': H or 240}),
                             'framerate': json.dumps({'framerate': {'numerator': 60.0, 'denominator': 1.0}})}}
else:
    V_SOURCE = {'id': 'v4l2_input', 'versioned_id': 'v4l2_input', 'name': 'MXO2', 'uuid': v_uuid,
                'enabled': True, 'settings': v_settings}
collection = {
    'name': 'mxo2test', 'current_scene': 'Scene', 'current_program_scene': 'Scene',
    'scene_order': [{'name': 'Scene'}], 'groups': [], 'transitions': [], 'current_transition': 'Fade',
    'transition_duration': 300, 'quick_transitions': [], 'saved_projectors': [], 'modules': {},
    'sources': [
        {'id': 'scene', 'versioned_id': 'scene', 'name': 'Scene', 'uuid': scene_uuid, 'enabled': True,
         'settings': {'id_counter': 2, 'custom_size': False, 'items': [
             {'name': 'MXO2', 'source_uuid': v_uuid, 'visible': True, 'locked': False, 'id': 1, 'rot': 0.0,
              'pos': {'x': 0.0, 'y': 0.0}, 'scale': {'x': 1.0, 'y': 1.0}, 'align': 5, 'bounds_type': 2,
              'bounds_align': 0, 'bounds': {'x': 1280.0, 'y': 720.0}, 'crop_left': 0, 'crop_top': 0,
              'crop_right': 0, 'crop_bottom': 0},
             {'name': 'MXO2 audio', 'source_uuid': a_uuid, 'visible': True, 'locked': False, 'id': 2,
              'rot': 0.0, 'pos': {'x': 0.0, 'y': 0.0}, 'scale': {'x': 1.0, 'y': 1.0}, 'align': 5,
              'bounds_type': 0, 'bounds_align': 0, 'bounds': {'x': 0.0, 'y': 0.0}}]}},
        V_SOURCE,
        {'id': 'alsa_input_capture', 'versioned_id': 'alsa_input_capture', 'name': 'MXO2 audio', 'uuid': a_uuid,
         'enabled': True, 'settings': {'device_id': 'hw:CARD=Mini,DEV=0', 'rate': 48000}},
    ]}
json.dump(collection, open(os.path.join(cfg, 'basic', 'scenes', 'mxo2test.json'), 'w'), indent=1)

env = dict(os.environ, XDG_CONFIG_HOME=os.path.join(WORK, 'config'))
env.setdefault('DISPLAY', ':0')
print('starting OBS (minimised, separate config in %s) for %g s' % (WORK, SECS), flush=True)
p = subprocess.Popen(['obs', '--collection', 'mxo2test', '--profile', 'mxo2test', '--startrecording',
                      '--minimize-to-tray', '--disable-shutdown-check', '--disable-missing-files-check'],
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=WORK)
time.sleep(SECS)
p.send_signal(signal.SIGINT)        # OBS quits cleanly on SIGINT (stops the recording first)
try:
    p.wait(20)
except subprocess.TimeoutExpired:
    p.kill()
    print('OBS did not exit on SIGINT, killed')

logs = sorted(glob.glob(os.path.join(cfg, 'logs', '*.txt')))
if logs:
    for line in open(logs[-1], errors='replace'):
        if any(k in line for k in ('v4l2', 'alsa', 'ALSA', 'Recording', 'recording', 'error', 'Error', 'camera',
                                   'pipewire', 'PipeWire')):
            print('  log: ' + line.rstrip())
files = sorted(glob.glob(os.path.join(rec, '*')))
if not files:
    print('RESULT: no recording')
    sys.exit(1)
f = files[-1]
print('recording %s (%d bytes)' % (f, os.path.getsize(f)))
probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                        'stream=codec_type,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration',
                        '-of', 'compact', f], capture_output=True, text=True)
print(probe.stdout.strip() or probe.stderr.strip())
png = os.path.join(WORK, 'frame.png')
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(max(1, SECS / 2)), '-i', f, '-frames:v', '1', png])
vol = subprocess.run(['ffmpeg', '-v', 'info', '-i', f, '-af', 'volumedetect', '-vn', '-f', 'null', '-'],
                     capture_output=True, text=True)
print('\n'.join(l for l in vol.stderr.splitlines() if 'mean_volume' in l or 'max_volume' in l))
print('frame: %s' % png)
