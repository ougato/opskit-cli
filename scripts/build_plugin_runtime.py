"""Source checkout wrapper for the generic isolated runtime builder."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import app

if __name__ == "__main__":
    app(args=["plugin", "prepare", *sys.argv[1:]])
