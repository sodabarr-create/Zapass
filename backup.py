"""Consistent SQLite backups. Run inside the app container."""
import datetime
import os
from pathlib import Path
import sqlite3
root=Path(os.environ.get('ZAPAS_DATA_DIR','/data'))
destination=root/'backups'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
destination.mkdir(parents=True,exist_ok=False)
for source in [root/'zapas.sqlite3',*sorted((root/'zapas_accounts').glob('*.sqlite3'))]:
    if not source.exists():continue
    target=destination/source.relative_to(root)
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source) as original,sqlite3.connect(target) as backup:
        original.backup(backup)
print('Backup:',destination)
