import sqlite3

conn = sqlite3.connect('data/engine.sqlite3')
cur = conn.cursor()

open_bets = cur.execute('SELECT count(*) FROM bets WHERE status = "open"').fetchone()[0]
print(f"Open bets count: {open_bets}")

open_decs = cur.execute('SELECT count(*) FROM system_decisions WHERE status = "open"').fetchone()[0]
print(f"Open decisions count: {open_decs}")

aged_decs = cur.execute('''
    SELECT count(*), min(issued_at), max(issued_at)
    FROM system_decisions
    WHERE status = "open" AND issued_at < datetime("now", "-48 hours")
''').fetchall()
print(f"Open decisions older than 48 hours: {aged_decs}")

if aged_decs[0][0] > 0:
    samples = cur.execute('''
        SELECT tip_id, family, issued_at, payload
        FROM system_decisions
        WHERE status = "open" AND issued_at < datetime("now", "-48 hours")
        LIMIT 5
    ''').fetchall()
    print("Sample aged open decisions:")
    for s in samples:
        print(f"  [{s[1]}] tip={s[0]} issued={s[2]}")
