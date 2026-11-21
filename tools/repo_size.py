"""Check the current Git commit's file bytes, or a not-yet-committed folder."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
try:
    names = subprocess.check_output(['git', 'ls-tree', '-r', '-l', 'HEAD'], cwd=root, stderr=subprocess.DEVNULL).decode().splitlines()
    total = sum(int(line.split()[3]) for line in names if line.split()[3].isdigit())
    source = 'HEAD tracked file bytes'
except (subprocess.CalledProcessError, FileNotFoundError):
    total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.git', '__pycache__')))
    source = 'working folder bytes (excluding Git/cache)'
print(f'{source}: {total:,} / 20,000,000')
sys.exit(0 if total <= 20_000_000 else 1)
