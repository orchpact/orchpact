"""Export a reviewed public snapshot into a new root commit and portable bundle.

Never stages files or changes HEAD/branches in the development repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(ROOT):
        raise ValueError('Output must be a new directory outside the source repository')
    spec = json.loads((ROOT / 'release/files.json').read_text())
    paths = set(spec['files'])
    for directory in spec['directories']:
        for path in (ROOT / directory).rglob('*'):
            relative = path.relative_to(ROOT)
            if any(part in spec['exclude_parts'] for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError('Symlink requires explicit review: ' + str(relative))
            if path.is_file():
                if path.suffix in spec['exclude_suffixes']:
                    continue
                if path.name.startswith('.env') and path.name != '.env.example':
                    continue
                paths.add(relative.as_posix())
    # Validate the entire list before creating any output or Git object.
    for name in paths:
        path = ROOT / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Unsafe or missing source: ' + name)
        if any(part in spec['exclude_parts'] for part in Path(name).parts):
            raise ValueError('Runtime path in public manifest: ' + name)
        if path.suffix in spec['exclude_suffixes'] or (path.name.startswith('.env') and path.name != '.env.example'):
            raise ValueError('Private file in public manifest: ' + name)
    for name in sorted(paths):
        if not name.endswith('.md'):
            continue
        for target in re.findall(r'\]\(([^)\s]+)', (ROOT / name).read_text()):
            if ':' in target or target.startswith(('#', '/')):
                continue
            linked = (ROOT / name).parent / target.split('#')[0]
            if not linked.resolve().is_relative_to(ROOT):
                raise ValueError('Documentation link leaves public source: ' + name)
            relative = linked.resolve().relative_to(ROOT).as_posix()
            if relative not in paths and not any(p.startswith(relative + '/') for p in paths):
                raise ValueError('Missing public documentation target: ' + name + ' -> ' + target)
    destination.mkdir(parents=True, mode=0o700)
    snapshot = destination / 'source'
    snapshot.mkdir()
    records = []
    for name in sorted(paths):
        source, target = ROOT / name, snapshot / name
        data = source.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        records.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    branch = 'release/' + spec['project'] + '-' + spec['candidate']

    def git(*args):
        return subprocess.check_output(['git', '-C', str(snapshot), *args], stderr=subprocess.STDOUT).decode().strip()

    git('init', '-b', branch)
    # The directory contains only the validated allowlist, with no old history.
    git('add', '--all')
    git('-c', 'user.name=Orchpact Release Candidate', '-c', 'user.email=release-candidate@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'feat: initialize Orchpact workflow engine')
    commit = git('rev-parse', 'HEAD')
    if set(git('ls-tree', '-r', '--name-only', 'HEAD').splitlines()) != paths:
        raise ValueError('Git tree does not exactly match the public allowlist')
    git('bundle', 'create', str(destination / 'orchpact.bundle'), branch, 'HEAD')
    report = dict(format=spec['format'], candidate=spec['candidate'], branch=branch, commit=commit, files=records)
    (destination / 'source-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(candidate=spec['candidate'], commit=commit, files=len(records), output=str(destination))))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        export(args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Git output may contain local paths, but never reads source credentials.
        parser.error(str(error))


if __name__ == '__main__':
    main()
