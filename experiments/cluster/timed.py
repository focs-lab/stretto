#!/usr/bin/env python3
"""/usr/bin/time -f '%e %M' -o FILE for a node without GNU time: run the command, then write its
elapsed seconds and its peak resident set in kB (the children's ru_maxrss) to FILE, and exit
with its status.
usage: timed.py -o FILE COMMAND [ARGS...]"""
import resource
import subprocess
import sys
import time

if len(sys.argv) < 4 or sys.argv[1] != "-o":
    sys.exit(__doc__)
t0 = time.monotonic()
rc = subprocess.call(sys.argv[3:])
el = time.monotonic() - t0
with open(sys.argv[2], "w") as fh:
    fh.write("%.2f %d\n" % (el, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
sys.exit(rc if rc >= 0 else 128 - rc)
