import sqlite3

conn = sqlite3.connect('db/platform.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('SELECT DISTINCT tts_provider FROM agents')
rows = cur.fetchall()
print('All TTS providers:', [r[0] for r in rows])

print()
cur.execute('SELECT id, name, tts_provider, voice, stt_provider FROM agents')
rows = cur.fetchall()
for r in rows:
    print(dict(r))
conn.close()
