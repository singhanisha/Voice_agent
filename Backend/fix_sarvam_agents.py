"""
fix_sarvam_agents.py — One-time migration to fix tts_provider for existing agents
that use Sarvam voice personas (shreya, ishita, shubh) but have tts_provider='edge'.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "platform.db")

SARVAM_VOICE_NAMES = ("shreya", "ishita", "shubh", "priya", "neha", "aditya", "ashutosh")

def fix_sarvam_agents():
    print(f"Opening DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in SARVAM_VOICE_NAMES)
    cur.execute(
        f"SELECT id, name, voice, tts_provider FROM agents WHERE LOWER(voice) IN ({placeholders})",
        SARVAM_VOICE_NAMES
    )
    rows = cur.fetchall()

    if not rows:
        print("No agents with Sarvam voice names found.")
        conn.close()
        return

    print(f"Found {len(rows)} agent(s) with Sarvam voice names:")
    to_fix = []
    for r in rows:
        row = dict(r)
        status = "OK" if row["tts_provider"] == "sarvam" else "WRONG"
        print(f"  [{status:5s}] {row['name']} | voice={row['voice']} | tts_provider={row['tts_provider']} | id={row['id'][:8]}...")
        if row["tts_provider"] != "sarvam":
            to_fix.append(row["id"])

    if not to_fix:
        print("\nAll agents already have tts_provider='sarvam'. Nothing to fix.")
        conn.close()
        return

    print(f"\nFixing {len(to_fix)} agent(s): setting tts_provider='sarvam'...")
    for agent_id in to_fix:
        cur.execute("UPDATE agents SET tts_provider='sarvam' WHERE id=?", (agent_id,))
        print(f"  Fixed agent {agent_id[:8]}...")

    conn.commit()

    cur.execute(
        f"SELECT id, name, voice, tts_provider FROM agents WHERE id IN ({','.join('?' for _ in to_fix)})",
        to_fix
    )
    fixed = cur.fetchall()
    print("\nVerification after fix:")
    for r in fixed:
        row = dict(r)
        ok = "OK" if row["tts_provider"] == "sarvam" else "FAIL"
        print(f"  [{ok}] {row['name']} | tts_provider={row['tts_provider']} | voice={row['voice']}")

    conn.close()
    print("\nDone! Sarvam agents have been fixed.")

if __name__ == "__main__":
    fix_sarvam_agents()
