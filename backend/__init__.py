"""Ticket Resolution Agent backend.

.env is loaded here, once, before any submodule reads configuration at
import time (auth.JWT_SECRET, the AI provider settings). Real environment
variables (e.g. App Service settings) always win over .env.
"""

from dotenv import load_dotenv

load_dotenv()
