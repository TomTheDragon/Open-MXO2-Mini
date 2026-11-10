"""Buffer mapping the way PipeWire's V4L2 source does it (spa/plugins/v4l2/v4l2-utils.c, 1.6): REQBUFS USERPTR
(expected EINVAL, not offered), then MMAP buffers mapped PROT_READ with MAP_SHARED (mmap_init) and MAP_PRIVATE
(the USERPTR fallback in userptr_init: needs the driver's mxo2_mmap), and EXPBUF + mmap of the DMA-BUF.
Runs as a user in the video group, device idle. Usage: python3 -I mmap_check.py [/dev/videoN]"""
import errno, fcntl, mmap, os, struct, sys


def _ioc(d, nr, size):
    return (d << 30) | (size << 16) | (ord('V') << 8) | nr


REQBUFS, QUERYBUF, EXPBUF = _ioc(3, 8, 20), _ioc(3, 9, 88), _ioc(3, 16, 64)
CAP, MMAP, USERPTR = 1, 1, 2


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '/dev/video0'
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    bad = 0
    r = bytearray(20)
    struct.pack_into('<III', r, 0, 4, CAP, USERPTR)
    try:
        fcntl.ioctl(fd, REQBUFS, r)
        print('REQBUFS USERPTR: ok')
    except OSError as e:
        print('REQBUFS USERPTR: %s (PipeWire then falls back to MMAP + MAP_PRIVATE)' % errno.errorcode[e.errno])
    struct.pack_into('<III', r, 0, 4, CAP, MMAP)
    fcntl.ioctl(fd, REQBUFS, r)
    n = struct.unpack_from('<I', r, 0)[0]
    for i in range(n):
        q = bytearray(88)
        struct.pack_into('<II', q, 0, i, CAP)
        struct.pack_into('<I', q, 60, MMAP)
        fcntl.ioctl(fd, QUERYBUF, q)
        off, length = struct.unpack_from('<I', q, 64)[0], struct.unpack_from('<I', q, 72)[0]
        res = []
        for flags, name in ((mmap.MAP_SHARED, 'shared'), (mmap.MAP_PRIVATE, 'private')):
            try:
                mmap.mmap(fd, length, flags, mmap.PROT_READ, offset=off).close()
                res.append(name + ' ok')
            except OSError as e:
                res.append('%s %s' % (name, errno.errorcode[e.errno]))
                bad += 1
        e = bytearray(64)
        struct.pack_into('<IIII', e, 0, CAP, i, 0, os.O_CLOEXEC | os.O_RDONLY)
        try:
            fcntl.ioctl(fd, EXPBUF, e)
            dfd = struct.unpack_from('<i', e, 16)[0]
            mmap.mmap(dfd, length, mmap.MAP_SHARED, mmap.PROT_READ).close()
            os.close(dfd)
            res.append('dmabuf ok')
        except OSError as x:
            res.append('dmabuf %s' % errno.errorcode[x.errno])
            bad += 1
        print('buffer %d (%d bytes): %s' % (i, length, ', '.join(res)))
    struct.pack_into('<III', r, 0, 0, CAP, MMAP)
    fcntl.ioctl(fd, REQBUFS, r)
    os.close(fd)
    print('RESULT: %s' % ('OK' if not bad else '%d mapping(s) failed' % bad))


main()
