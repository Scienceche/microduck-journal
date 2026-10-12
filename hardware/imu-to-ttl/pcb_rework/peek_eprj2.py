#!/usr/bin/env python3
"""Explore the SQLite-backed .eprj2 project the user downloaded (imu_to_dxl.eprj2).

Usage: python peek_eprj2.py <file.eprj2> [table]
Without a table name it prints the schema and small row previews so we can
locate the schematic/PCB documents (imu_to_feetech / imu_to_oxi / Panel1).
"""

import sys
import sqlite3

path = sys.argv[1]
con = sqlite3.connect(path)
cur = con.cursor()

if len(sys.argv) < 3:
    rows = cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('table','view') "
        "ORDER BY name").fetchall()
    for name, sql in rows:
        print("== %s" % name)
        print("   %s" % (sql or "").replace("\n", " ")[:200])
        try:
            cnt = cur.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
            print("   rows: %d" % cnt)
            sample = cur.execute(
                "SELECT * FROM %s LIMIT 3" % name).fetchall()
            for r in sample:
                cells = []
                for c in r:
                    s = str(c)
                    cells.append(s[:60] + ("..." if len(s) > 60 else ""))
                print("   | " + " | ".join(cells))
        except Exception as e:
            print("   (no peek: %s)" % e)
    sys.exit(0)

table = sys.argv[2]
cols = [d[1] for d in cur.execute("PRAGMA table_info(%s)" % table).fetchall()]
print("columns:", cols)
n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
for r in cur.execute("SELECT * FROM %s LIMIT %d" % (table, n)).fetchall():
    for cname, c in zip(cols, r):
        s = str(c)
        print("  %-16s %s" % (cname, s[:200] + ("..." if len(s) > 200 else "")))
    print("  " + "-" * 60)
