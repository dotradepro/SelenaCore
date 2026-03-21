import os
import sys
from pathlib import Path

# Module root: modules/weather-module/
MODULE_ROOT  = Path(__file__).parent.parent
# Project root: SelenaCore/
PROJECT_ROOT = MODULE_ROOT.parent.parent

# Make both importable
sys.path.insert(0, str(MODULE_ROOT))   # for: main, weather_client
sys.path.insert(0, str(PROJECT_ROOT))  # for: sdk.base_module

# Set test env vars before any module import
os.environ["WEATHER_CONFIG_PATH"] = "/tmp/weather-test.json"
os.environ.setdefault("SELENA_CORE_API",   "http://localhost:7070/api/v1")
os.environ.setdefault("MODULE_TOKEN",      "test-token")
os.environ.setdefault("DEV_MODULE_TOKEN",  "test-token")
os.environ.setdefault("SELENA_MODULE_PORT","8100")
