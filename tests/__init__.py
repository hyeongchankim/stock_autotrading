# Tests must never write the real names_cache.json (fake quote rows would put fake names into the live logs).
import tempfile
from pathlib import Path

from utils import names

names.NAMES_FILE = Path(tempfile.mkdtemp()) / "names_cache.json"
