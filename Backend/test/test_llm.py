"""
Smoke-test for the LLM module.
Run from the project root:  python test_llm.py

Requires GROQ_API_KEY to be set in your environment:
    $env:GROQ_API_KEY = "gsk_your_key_here"
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

print("=" * 55)
print("       AI Voice Agent — LLM Module Test")
print("=" * 55)

# ── 1. Import check ───────────────────────────────────────
print("\n[1/3] Importing LLM module ...")
try:
    from llm import generate_response
    print("     OK: Module imported successfully.")
except EnvironmentError as e:
    print(f"\n  ERROR: {e}")
    sys.exit(1)
except Exception as e:
    print(f"\n  ERROR: Import failed: {e}")
    sys.exit(1)

# ── 2. Empty input test (should return "") ────────────────
print("\n[2/3] Testing empty input (should return empty string) ...")
result = generate_response("")
if result == "":
    print("     OK: Empty input handled correctly.")
else:
    print(f"     WARNING: Expected empty string, got: '{result}'")

# ── 3. Real API call ──────────────────────────────────────
print("\n[3/3] Calling Groq API with a test prompt ...")
test_prompt = "Hi, I'm calling about a 2BHK apartment you were interested in."

t0 = time.time()
response = generate_response(test_prompt, language="en")
latency = time.time() - t0

print(f"\n     Prompt   : '{test_prompt}'")
print(f"     Response : '{response}'")
print(f"     Latency  : {latency:.3f}s")

# ── Summary ───────────────────────────────────────────────
print("\n" + "=" * 55)
print("  Summary")
print("=" * 55)
latency_ok = latency < 1.0
print(f"  Got a response : {'YES' if response else 'NO (empty)'}")
print(f"  Latency target : {'OK within 1.0s' if latency_ok else f'WARNING exceeded 1.0s ({latency:.3f}s)'}")
print("\n  Basic LLM sanity-check complete.\n")
