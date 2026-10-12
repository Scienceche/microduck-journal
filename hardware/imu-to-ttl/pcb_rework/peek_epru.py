#!/usr/bin/env python3
"""Summarize an EasyEDA Pro .epru document stream: one JSON object per '||'.

Prints each object's docType/title/name and, for PCB docs, the component
designators + names so we can tell which board variant the file holds.
"""

import json
import sys

raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
parts = raw.split("||")
print("objects: %d" % len(parts))

for i, p in enumerate(parts):
    p = p.strip()
    if not p:
        continue
    try:
        obj = json.loads(p)
    except Exception as e:
        print("[%d] PARSE FAIL (%s) head=%r" % (i, str(e)[:40], p[:80]))
        continue
    dt = obj.get("docType") or obj.get("type")
    title = obj.get("title") or obj.get("name") or ""
    keys = sorted(obj.keys())
    print("[%d] docType=%s title=%r keys=%s" % (i, dt, title, ",".join(keys)[:120]))
