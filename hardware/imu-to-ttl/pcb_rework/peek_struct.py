#!/usr/bin/env python3
"""Print the newest project_structures row (document tree) of an .eprj2."""

import json
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
cur = con.cursor()
cols = [d[1] for d in cur.execute("PRAGMA table_info(project_structures)").fetchall()]
print("columns:", cols)
row = cur.execute(
    "SELECT * FROM project_structures ORDER BY id DESC LIMIT 1").fetchone()
d = dict(zip(cols, row))
for k, v in d.items():
    if isinstance(v, str) and v.startswith("{"):
        tree = json.loads(v)
        print(json.dumps(tree, ensure_ascii=False, indent=1)[:4000])
    else:
        print("%s: %s" % (k, v))
