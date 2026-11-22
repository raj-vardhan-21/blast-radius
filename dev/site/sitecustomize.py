import os

if os.environ.get("TRACE_CHILD") == "1" and os.environ.get("TRACE_OUT"):
    import tracer  # noqa: F401
