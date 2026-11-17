"""Register a definition and explicitly bind a consuming application through the SDK."""
import sys
from orchpact.interfaces.client import configure

if __name__=='__main__':
    if len(sys.argv)<2:raise SystemExit('Usage: register-workflow.py DEFINITION.json --application APP [--install --bindings FILE]')
    configure(sys.argv[1],sys.argv[2:])
