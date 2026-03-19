"""Quick smoke-test: verify Langfuse connectivity and push a test trace."""
import os
from dotenv import load_dotenv

load_dotenv()

SECRET = os.getenv("LANGFUSE_SECRET_KEY", "")
PUBLIC = os.getenv("LANGFUSE_PUBLIC_KEY", "")
HOST   = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000")

print(f"Host:       {HOST}")
print(f"Public key: {PUBLIC[:12]}..." if PUBLIC else "Public key: (not set)")
print(f"Secret key: {SECRET[:12]}..." if SECRET else "Secret key: (not set)")
print()

# 1. Raw HTTP health check
import urllib.request, urllib.error
try:
    with urllib.request.urlopen(f"{HOST}/api/public/health", timeout=5) as r:
        body = r.read().decode()
        print(f"[OK] Health endpoint: HTTP {r.status} — {body}")
except urllib.error.URLError as e:
    print(f"[FAIL] Health endpoint unreachable: {e.reason}")
    print("       Is the Langfuse Docker stack running?")
    raise SystemExit(1)

print()

# 2. Push a real trace via the SDK (v4 API)
from langfuse import get_client

lf = get_client()

# auth_check verifies credentials before sending anything
if not lf.auth_check():
    print("[FAIL] auth_check() failed — bad API keys or wrong host")
    raise SystemExit(1)
print("[OK] auth_check() passed")

with lf.start_as_current_observation(
    as_type="span",
    name="test-trace",
    input={"message": "hello from hr_ai smoke test"},
) as span:
    span.update(
        output={"result": "trace pushed successfully"},
        metadata={"tags": ["smoke-test"]},
    )
    trace_id = span.trace_id

lf.flush()
print(f"[OK] Trace pushed! ID: {trace_id}")
print(f"     View at: {HOST}/traces/{trace_id}")
