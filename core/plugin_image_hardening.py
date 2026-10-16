"""Fixed build-time hardening for platform SDK and egress images."""

HARDENING_SCRIPT = """import os, stat
for base in ('/usr/bin', '/usr/sbin', '/usr/local/bin'):
    for parent, dirs, files in os.walk(base, followlinks=False):
        for name in files:
            path = os.path.join(parent, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISREG(mode) and mode & 0o6000:
                os.chmod(path, stat.S_IMODE(mode) & ~0o6000)
path = '/usr/bin/infocmp'
if os.path.lexists(path):
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise RuntimeError('unexpected gateway utility path type')
    os.unlink(path)
"""
