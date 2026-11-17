"""Own only the explicitly mounted standalone instance; credentials never go to logs."""
import os
from pathlib import Path
import sys
from orchpact.cli import initialize

root=Path('/var/lib/orchpact')
initialize(root)
os.execvp('orchpact',['orchpact','--root',str(root),*sys.argv[1:]])
