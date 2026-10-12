#!/usr/bin/env python3
"""Save the project_images WebP previews of an .eprj2 and probe history blobs."""

import base64
import os
import sqlite3
import sys
import zlib

path, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)
con = sqlite3.connect(path)
cur = con.cursor()

for uuid, url, blob in cur.execute(
        "SELECT uuid, url, image_data FROM project_images").fetchall():
    data = base64.b64decode(blob)
    fn = os.path.join(outdir, "eprj2_img_%s.webp" % uuid)
    open(fn, "wb").write(data)
    print("wrote %s (%d bytes) url=%s" % (fn, len(data), url))

print("---- history_data blob probe ----")
for uuid, hu, ds in cur.execute(
        "SELECT uuid, history_uuid, dataStr FROM history_data "
        "ORDER BY LENGTH(dataStr) DESC LIMIT 5").fetchall():
    raw = base64.b64decode(ds)
    kinds = []
    for name, fn in (("zlib", lambda b: zlib.decompress(b)),
                     ("deflate", lambda b: zlib.decompressobj(-15).decompress(b))):
        try:
            out = fn(raw)
            kinds.append("%s->%d bytes, head=%r" % (name, len(out), out[:40]))
        except Exception as e:
            kinds.append("%s:no(%s)" % (name, str(e)[:30]))
    print("%s hu=%s len=%d rawhead=%s" % (uuid, hu, len(ds), raw[:6].hex()))
    for k in kinds:
        print("    " + k)
