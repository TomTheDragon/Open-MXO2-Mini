#!/usr/bin/env python3
"""Replay the V4L2 call sequence of OBS Studio's linux-v4l2 source against the MXO2 (no root needed, video group).

OBS (plugins/linux-v4l2/v4l2-input.c, v4l2-helpers.c): device scan (QUERYCAP, needs VIDEO_CAPTURE + STREAMING in
device_caps), properties (ENUMINPUT, G_INPUT, ENUM_FMT, ENUM_FRAMESIZES, ENUM_FRAMEINTERVALS per size), then
v4l2_init: open O_RDWR|O_NONBLOCK, S_INPUT, (S_STD / S_DV_TIMINGS only if the input says so), G_FMT -> S_FMT,
G_PARM -> S_PARM, G_FMT, REQBUFS 4 mmap, QUERYBUF + mmap, QBUF all, STREAMON, select(1 s) + DQBUF + QBUF loop.
While streaming, a second handle does what the properties dialog does.

Usage: python3 -I obs_sim.py [/dev/videoN] [input, always 0] [width height] [seconds]
"""
import errno
import fcntl
import mmap
import os
import select
import struct
import sys
import time


def _ioc(d, nr, size):
    return d << 30 | size << 16 | ord('V') << 8 | nr


R, W, RW = 2, 1, 3
QUERYCAP, ENUM_FMT, G_FMT, S_FMT = _ioc(R, 0, 104), _ioc(RW, 2, 64), _ioc(RW, 4, 208), _ioc(RW, 5, 208)
REQBUFS, QUERYBUF, QBUF, DQBUF = _ioc(RW, 8, 20), _ioc(RW, 9, 88), _ioc(RW, 15, 88), _ioc(RW, 17, 88)
STREAMON, STREAMOFF = _ioc(W, 18, 4), _ioc(W, 19, 4)
G_PARM, S_PARM = _ioc(RW, 21, 204), _ioc(RW, 22, 204)
ENUMINPUT, G_INPUT, S_INPUT = _ioc(RW, 26, 80), _ioc(R, 38, 4), _ioc(RW, 39, 4)
ENUM_FRAMESIZES, ENUM_FRAMEINTERVALS = _ioc(RW, 74, 44), _ioc(RW, 75, 52)
CAP, STREAMING, DEVICE_CAPS = 0x1, 0x04000000, 0x80000000
BUF_CAP, MMAP_MEM = 1, 1
IN_CAP_DV_TIMINGS, IN_CAP_STD = 0x2, 0x4
YUYV = struct.unpack('<I', b'YUYV')[0]
FIELDS = {0: 'any', 1: 'none', 2: 'top', 3: 'bottom', 4: 'interlaced', 5: 'seq_tb', 6: 'seq_bt', 7: 'alternate',
          8: 'interlaced_tb', 9: 'interlaced_bt'}
problems = []


def ioctl(fd, req, buf):
    fcntl.ioctl(fd, req, buf, True)
    return buf


def problem(msg):
    problems.append(msg)
    print('  PROBLEM: ' + msg, flush=True)


def scan(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        b = ioctl(fd, QUERYCAP, bytearray(104))
        driver, card = b[:16].split(b'\0')[0].decode(), b[16:48].split(b'\0')[0].decode()
        caps, dcaps = struct.unpack_from('<II', b, 84)
        use = dcaps if caps & DEVICE_CAPS else caps
        ok = bool(use & CAP and use & STREAMING)
        print('device %s: driver %s, card "%s", device_caps %08x -> %s' % (
            path, driver, card, use, 'listed by OBS' if ok else 'SKIPPED by OBS'))
        if not ok:
            problem('OBS would not list the device')
    finally:
        os.close(fd)


def inputs(fd):
    out, i = [], 0
    while True:
        b = bytearray(80)
        struct.pack_into('<I', b, 0, i)
        try:
            ioctl(fd, ENUMINPUT, b)
        except OSError:
            break
        name = b[4:36].split(b'\0')[0].decode()
        status, icaps = struct.unpack_from('<II', b, 56)
        out.append((i, name, status, icaps))
        i += 1
    return out


def g_input(fd):
    return struct.unpack('<i', ioctl(fd, G_INPUT, bytearray(4)))[0]


def s_input(fd, i):
    ioctl(fd, S_INPUT, bytearray(struct.pack('<i', i)))


def formats(fd):
    out, i = [], 0
    while True:
        b = bytearray(64)
        struct.pack_into('<II', b, 0, i, BUF_CAP)
        try:
            ioctl(fd, ENUM_FMT, b)
        except OSError:
            break
        out.append((struct.unpack_from('<I', b, 44)[0], b[12:44].split(b'\0')[0].decode()))
        i += 1
    return out


def sizes(fd, pf):
    out, i = [], 0
    while True:
        b = bytearray(44)
        struct.pack_into('<II', b, 0, i, pf)
        try:
            ioctl(fd, ENUM_FRAMESIZES, b)
        except OSError:
            break
        t, w, h = struct.unpack_from('<III', b, 8)
        if t != 1:
            problem('frame size type %d (OBS expects discrete here)' % t)
        out.append((w, h))
        i += 1
    return out


def intervals(fd, pf, w, h):
    out, i = [], 0
    while True:
        b = bytearray(52)
        struct.pack_into('<IIII', b, 0, i, pf, w, h)
        try:
            ioctl(fd, ENUM_FRAMEINTERVALS, b)
        except OSError:
            break
        t, num, den = struct.unpack_from('<III', b, 16)
        out.append((num, den))
        i += 1
    return out


def g_fmt(fd):
    b = bytearray(208)
    struct.pack_into('<I', b, 0, BUF_CAP)
    ioctl(fd, G_FMT, b)
    return b


def fmt_str(b):
    w, h, pf, field, bpl, size, cs = struct.unpack_from('<7I', b, 8)
    q = struct.unpack_from('<I', b, 8 + 40)[0]
    return w, h, pf, field, bpl, size, '%dx%d %s field %s bpl %d size %d colorspace %d quant %d' % (
        w, h, struct.pack('<I', pf).decode(), FIELDS.get(field, field), bpl, size, cs, q)


def properties(fd, label):
    """What OBS' properties dialog asks (also on a second handle while streaming)."""
    ins = inputs(fd)
    cur = g_input(fd)
    print('  [%s] inputs: %s; current %d' % (label, ', '.join(
        '%d %s%s%s' % (i, n, ' (no signal)' if st & 2 else '', ' STD' if c & IN_CAP_STD else '') for i, n, st, c in ins),
        cur), flush=True)
    for pf, desc in formats(fd):
        szs = sizes(fd, pf)
        print('  [%s] format %s "%s": %s' % (label, struct.pack('<I', pf).decode(), desc, ', '.join(
            '%dx%d@%s' % (w, h, '/'.join('%g' % (d / n) for n, d in intervals(fd, pf, w, h)) or 'NONE')
            for w, h in szs)), flush=True)
        for w, h in szs:
            if not intervals(fd, pf, w, h):
                problem('no frame interval for %dx%d (OBS framerate list empty)' % (w, h))
    return ins


def other_device(path):
    """The card's other video device (HDMI <-> Composite), by sysfs name."""
    me = os.path.basename(path)
    name = open('/sys/class/video4linux/%s/name' % me).read().strip()
    for d in sorted(os.listdir('/sys/class/video4linux')):
        n = open('/sys/class/video4linux/%s/name' % d).read().strip()
        if d != me and n.startswith('Matrox MXO2') and n != name:
            return '/dev/' + d
    return None


def try_stream(path):
    """REQBUFS 2 + QBUF + STREAMON on another handle; returns 0 or the errno."""
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        r = bytearray(20)
        struct.pack_into('<III', r, 0, 2, BUF_CAP, MMAP_MEM)
        ioctl(fd, REQBUFS, r)
        for i in range(struct.unpack_from('<I', r, 0)[0]):
            q = bytearray(88)
            struct.pack_into('<III', q, 0, i, BUF_CAP, 0)
            struct.pack_into('<I', q, 60, MMAP_MEM)
            ioctl(fd, QUERYBUF, q)
            ioctl(fd, QBUF, q)
        ioctl(fd, STREAMON, bytearray(struct.pack('<I', BUF_CAP)))
        ioctl(fd, STREAMOFF, bytearray(struct.pack('<I', BUF_CAP)))
        return 0
    except OSError as e:
        return e.errno
    finally:
        os.close(fd)


def stream(path, inp, width, height, secs):
    print('--- OBS v4l2_init: input %d, %s, %g s' % (inp, '%dx%d' % (width, height) if width else 'current size',
                                                       secs), flush=True)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    maps = []
    try:
        s_input(fd, inp)
        icaps = [c for i, n, st, c in inputs(fd) if i == inp][0]
        if icaps & (IN_CAP_STD | IN_CAP_DV_TIMINGS):
            print('  input has STD/DV caps: OBS would call S_STD/S_DV_TIMINGS (not supported by this sim)')
        b = g_fmt(fd)
        if width:
            struct.pack_into('<II', b, 8, width, height)
        struct.pack_into('<I', b, 16, YUYV)
        ioctl(fd, S_FMT, b)
        p = bytearray(204)
        struct.pack_into('<I', p, 0, BUF_CAP)
        ioctl(fd, G_PARM, p)
        ioctl(fd, S_PARM, p)
        num, den = struct.unpack_from('<II', p, 12)
        w, h, pf, field, bpl, size, desc = fmt_str(g_fmt(fd))
        print('  format: %s, %g fps' % (desc, den / num if num else 0), flush=True)
        if width and (w, h) != (width, height):
            problem('asked %dx%d, got %dx%d' % (width, height, w, h))
        r = bytearray(20)
        struct.pack_into('<III', r, 0, 4, BUF_CAP, MMAP_MEM)
        ioctl(fd, REQBUFS, r)
        n = struct.unpack_from('<I', r, 0)[0]
        for i in range(n):
            q = bytearray(88)
            struct.pack_into('<III', q, 0, i, BUF_CAP, 0)
            struct.pack_into('<I', q, 60, MMAP_MEM)
            ioctl(fd, QUERYBUF, q)
            off, length = struct.unpack_from('<IxxxxI', q, 64)
            maps.append(mmap.mmap(fd, length, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=off))
            ioctl(fd, QBUF, q)
        print('  %d buffers of %d bytes' % (n, length), flush=True)
        ioctl(fd, STREAMON, bytearray(struct.pack('<I', BUF_CAP)))
        frames, timeouts, last_ts, last_seq, gaps, bad = 0, 0, None, None, [], 0
        t0 = time.monotonic()
        props_done = False
        while time.monotonic() - t0 < secs:
            rd, _, _ = select.select([fd], [], [], 1.0)
            if not rd:
                timeouts += 1
                print('  select timed out (OBS: "select timed out")', flush=True)
                continue
            q = bytearray(88)
            struct.pack_into('<II', q, 0, 0, BUF_CAP)
            struct.pack_into('<I', q, 60, MMAP_MEM)
            try:
                ioctl(fd, DQBUF, q)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                raise
            idx, _, used, flags, bfield = struct.unpack_from('<5I', q, 0)
            sec, usec = struct.unpack_from('<qq', q, 24)
            seq = struct.unpack_from('<I', q, 56)[0]
            ts = sec + usec / 1e6
            if flags & 0x40 or used != size:      # V4L2_BUF_FLAG_ERROR
                bad += 1
            if last_ts is not None:
                gaps.append(ts - last_ts)
                if seq != last_seq + 1:
                    problem('sequence jump %d -> %d' % (last_seq, seq))
            last_ts, last_seq = ts, seq
            if frames == 0:
                y = maps[idx][0:size:2]
                print('  first frame: bytesused %d field %s luma min/mean/max %d/%.0f/%d' % (
                    used, FIELDS.get(bfield, bfield), min(y), sum(y) / len(y), max(y)), flush=True)
            frames += 1
            ioctl(fd, QBUF, q)
            if not props_done and time.monotonic() - t0 > secs / 2:
                fd2 = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                try:
                    properties(fd2, 'props while streaming')
                    s_input(fd2, inp)       # OBS re-applies the selected input
                    other = other_device(path)
                    if other:
                        e = try_stream(other)
                        if e == 0:
                            problem('the other device %s started streaming while %s streams' % (other, path))
                        else:
                            print('  [props while streaming] STREAMON on %s -> %s (expected EBUSY)' %
                                  (other, errno.errorcode.get(e, e)), flush=True)
                finally:
                    os.close(fd2)
                props_done = True
        el = time.monotonic() - t0
        ioctl(fd, STREAMOFF, bytearray(struct.pack('<I', BUF_CAP)))
        if gaps:
            gaps.sort()
            print('  %d frames in %.1f s = %.2f fps, interval median %.2f ms max %.2f ms, %d select timeouts, '
                  '%d error/short buffers' % (frames, el, frames / el, 1000 * gaps[len(gaps) // 2], 1000 * gaps[-1],
                                              timeouts, bad), flush=True)
            if gaps[0] <= 0:
                problem('timestamps not increasing')
        if not frames:
            problem('no frames')
        if bad:
            problem('%d buffers with error flag / wrong size' % bad)
        return frames
    finally:
        for m in maps:
            m.close()
        os.close(fd)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '/dev/video0'
    inp = int(sys.argv[2]) if len(sys.argv) > 2 else 0      # one input per device
    w, h = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (0, 0)
    secs = float(sys.argv[5]) if len(sys.argv) > 5 else 5
    scan(path)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        before = g_input(fd)
        print('--- properties (input %d at start)' % before)
        properties(fd, 'props')
        s_input(fd, inp)                    # selecting the input in the dialog
        time.sleep(1)
        properties(fd, 'props after S_INPUT %d' % inp)
    finally:
        os.close(fd)
    stream(path, inp, w, h, secs)
    print('RESULT: %s' % ('OK' if not problems else '%d problem(s): %s' % (len(problems), '; '.join(problems))))


if __name__ == '__main__':
    main()
