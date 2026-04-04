"""Smoke-test: verify all tracing backends can initialise."""
import os
from dotenv import load_dotenv

load_dotenv()

print("=== Tracing Backend Status ===\n")

# ── LangSmith ──────────────────────────────────────────────
print("[LangSmith]")
ls_enabled = os.getenv("ENABLE_LANGSMITH", "false").lower() == "true"
ls_key = os.getenv("LANGSMITH_API_KEY", "") or os.getenv("LANGCHAIN_API_KEY", "")
print(f"  Enabled:  {ls_enabled}")
print(f"  API key:  {'set' if ls_key.strip() else 'NOT SET'}")
print(f"  Project:  {os.getenv('LANGSMITH_PROJECT', os.getenv('LANGCHAIN_PROJECT', 'default'))}")
if ls_enabled and ls_key.strip():
    try:
        from langsmith import Client
        client = Client()
        # Just verify the client can be created
        print(f"  SDK:      langsmith (ok)")
    except ImportError:
        print("  SDK:      NOT INSTALLED (pip install langsmith)")
    except Exception as e:
        print(f"  SDK:      error — {e}")
elif ls_enabled:
    print("  Status:   ENABLED but missing API key")
else:
    print("  Status:   disabled")
print()

# ── Langfuse ───────────────────────────────────────────────
print("[Langfuse]")
lf_enabled = os.getenv("ENABLE_LANGFUSE", "false").lower() == "true"
lf_secret = os.getenv("LANGFUSE_SECRET_KEY", "")
lf_public = os.getenv("LANGFUSE_PUBLIC_KEY", "")
lf_host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
print(f"  Enabled:  {lf_enabled}")
print(f"  Host:     {lf_host}")
print(f"  Keys:     {'set' if lf_secret.strip() and lf_public.strip() else 'NOT SET'}")
if lf_enabled and lf_secret.strip() and lf_public.strip():
    try:
        from langfuse import get_client
        lf = get_client()
        if lf.auth_check():
            print("  Auth:     PASSED")
        else:
            print("  Auth:     FAILED — check keys or host URL")
    except ImportError:
        print("  SDK:      NOT INSTALLED (pip install langfuse)")
    except Exception as e:
        print(f"  Auth:     error — {e}")
elif lf_enabled:
    print("  Status:   ENABLED but missing keys")
else:
    print("  Status:   disabled")
print()

# ── Galileo AI ─────────────────────────────────────────────
print("[Galileo AI]")
gal_enabled = os.getenv("ENABLE_GALILEO", "false").lower() == "true"
gal_key = os.getenv("GALILEO_API_KEY", "")
gal_project = os.getenv("GALILEO_PROJECT", "hr-recruitment-agent")
print(f"  Enabled:  {gal_enabled}")
print(f"  API key:  {'set' if gal_key.strip() else 'NOT SET'}")
print(f"  Project:  {gal_project}")
if gal_enabled and gal_key.strip():
    try:
        from galileo import galileo_context
        galileo_context.init(project=gal_project)
        print("  SDK:      galileo (ok)")
    except ImportError:
        print("  SDK:      NOT INSTALLED (pip install 'galileo[langchain]')")
    except Exception as e:
        print(f"  SDK:      error — {e}")
elif gal_enabled:
    print("  Status:   ENABLED but missing API key")
else:
    print("  Status:   disabled")
print()

# ── Unified check ──────────────────────────────────────────
print("=== configure_tracing() ===\n")
from src.observability.tracing import configure_tracing
status = configure_tracing()
for backend, ok in status.items():
    icon = "OK" if ok else "--"
    print(f"  [{icon}] {backend}")
