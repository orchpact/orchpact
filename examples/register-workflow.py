"""Publish a workflow and optionally install its resources into a Workspace."""
import sys
from orchpact.interfaces.client import configure

if __name__=='__main__':
    if len(sys.argv)<2:raise SystemExit('Usage: register-workflow.py DEFINITION.json [--install --bindings FILE]')
    configure(sys.argv[1],sys.argv[2:])
