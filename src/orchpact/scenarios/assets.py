"""Read the same authored assets in an editable checkout and in the wheel."""
import json
from pathlib import Path


def read_asset(folder, filename):
    bundled = Path(__file__).parent / 'assets' / folder / filename
    source = Path(__file__).resolve().parents[3] / 'examples' / folder / filename
    return json.loads((bundled if bundled.is_file() else source).read_text(encoding='utf-8'))


def workflow(folder):
    return read_asset(folder, 'workflow.json')
