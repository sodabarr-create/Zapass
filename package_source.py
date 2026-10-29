"""Package only reviewed source paths; never archive the workspace wholesale."""
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
FILES=['app.py','accounts.py','billing.py','bot_content.py','catalog.py','deployment.py','economics.py','planner.py','studio.py','telegram_app.py','vault.py','README.md','requirements.txt','Dockerfile','compose.yaml','.dockerignore','.gitignore','.env.example']
DIRECTORIES=['static','deploy','scripts','tests','docs','.github']
def build(destination):
    paths=[ROOT/name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            if path.is_symlink():raise ValueError('Symlinks are not included.')
            archive.write(path,path.relative_to(ROOT).as_posix())
    return len(paths)
if __name__=='__main__':
    output=ROOT/'output'/'hosting';output.mkdir(parents=True,exist_ok=True)
    destination=output/'Zapas-GitHub-source.zip'
    print(destination,build(destination),'files')
