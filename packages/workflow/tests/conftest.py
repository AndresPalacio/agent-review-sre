"""Offline tests never send traces outside the process."""

import os

os.environ["LANGSMITH_TRACING"] = "false"
