"""Verified cold backups. Requires frozen writers and never restarts services.

The private JSON plan pins each source container ID/image and each mounted store.
Use a new output directory; restore archives only into new, isolated destinations.
This deliberately does not compose two independently restarting backup routines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import subprocess
import tarfile
from datetime import datetime, timezone


STORE_MANIFEST = r'''
import hashlib,json,os,pathlib,stat
root=pathlib.Path('/source'); records=[]
for p in [root, *sorted(root.rglob('*'))]:
    s=p.lstat(); item={'path':p.relative_to(root).as_posix(), 'mode':stat.S_IMODE(s.st_mode),
        'uid':s.st_uid,'gid':s.st_gid,'mtime':int(s.st_mtime)}
    if p.is_symlink(): item.update(type='symlink',link=os.readlink(p))
    elif p.is_dir(): item.update(type='directory')
    elif p.is_file():
        with p.open('rb') as f: h=hashlib.file_digest(f,'sha256').hexdigest()
        item.update(type='file',size=s.st_size,sha256=h)
    else: raise RuntimeError('Unsupported special file: '+str(p))
    records.append(item)
print(json.dumps(records,sort_keys=True))
'''


def write_json(path: Path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write('\n')


class Docker:
    def __init__(self, context='desktop-linux'):
        self.context = context

    def call(self, args, *, destination: Path | None = None):
        command = ['docker', '--context', self.context, *args]
        # Files are binary streams; never pass tar/pg_dump through shell text encoding.
        if destination is not None:
            with destination.open('xb') as output:
                result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE,
                                        timeout=1200, check=False)
        else:
            result = subprocess.run(command, capture_output=True, timeout=120, check=False)
        if result.returncode:
            raise RuntimeError('Docker operation failed: ' + result.stderr.decode(errors='replace'))
        return b'' if destination is not None else result.stdout

    def inspect(self, names):
        return json.loads(self.call(['inspect', *names]))


def normalize_bind(path):
    path = str(path).replace('\\', '/').rstrip('/')
    return path.casefold() if re.match(r'^[A-Za-z]:/', path) else path


def overlaps(mount, store):
    if mount['Type'] != store['type']:
        return False
    if store['type'] == 'volume':
        return mount.get('Name') == store['source']
    left, right = normalize_bind(mount['Source']), normalize_bind(store['source'])
    return left == right or left.startswith(right + '/') or right.startswith(left + '/')


def validate_plan(plan):
    if plan.get('schema') != 'daibm.cold-backup.v1':
        raise ValueError('Unknown backup plan schema')
    if not plan.get('containers') or not plan.get('stores'):
        raise ValueError('Explicit containers and stores are required')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', plan.get('helper_image', '')):
        raise ValueError('Helper must be pinned to a local image ID')
    for name, item in plan['containers'].items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid container name')
        if not re.fullmatch(r'[0-9a-f]{64}', item['id']):
            raise ValueError('Full source container ID required')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', item['image']):
            raise ValueError('Full source image ID required')
    names = set()
    for store in plan['stores']:
        if not re.fullmatch(r'[a-z][a-z0-9-]*', store['name']) or store['name'] in names:
            raise ValueError('Unique safe archive name required')
        names.add(store['name'])
        if store['container'] not in plan['containers']:
            raise ValueError('Store owner must be pinned in the plan')
        if store['type'] not in ('bind', 'volume') or ',' in store['source']:
            raise ValueError('Unsupported source mount')
        if store['type'] == 'volume':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', store['source']):
                raise ValueError('Invalid volume name')
        elif not Path(store['source']).is_absolute() or len(Path(store['source']).parts) < 4:
            raise ValueError('Bind source must be an explicit bounded absolute directory')


def assert_frozen(docker, plan):
    sources = docker.inspect(list(plan['containers']))
    by_name = {item['Name'].lstrip('/'): item for item in sources}
    for name, expected in plan['containers'].items():
        item = by_name[name]
        if item['Id'] != expected['id'] or item['Image'] != expected['image']:
            raise RuntimeError('Source identity/image changed: ' + name)
        if item['State']['Running'] or item['State'].get('Restarting') or item['State'].get('Paused'):
            raise RuntimeError('Source is not frozen: ' + name)
        if item['HostConfig']['RestartPolicy']['Name'] not in ('no', ''):
            raise RuntimeError('Automatic restart can violate the freeze: ' + name)
    for store in plan['stores']:
        matches = [m for m in by_name[store['container']]['Mounts']
                   if m['Destination'] == store['destination'] and m['Type'] == store['type']]
        if len(matches) != 1:
            raise RuntimeError('Source mount is missing or ambiguous: ' + store['name'])
        actual = matches[0].get('Name') if store['type'] == 'volume' else matches[0]['Source']
        matches_source = (actual == store['source'] if store['type'] == 'volume'
                          else normalize_bind(actual) == normalize_bind(store['source']))
        if not matches_source:
            raise RuntimeError('Source mount differs from plan: ' + store['name'])
    running = docker.call(['ps', '-q']).decode().split()
    for item in docker.inspect(running) if running else []:
        if any(m.get('RW') and overlaps(m, s) for m in item['Mounts'] for s in plan['stores']):
            raise RuntimeError('A running container can write the backup source: ' + item['Name'])
    return sources


def store_args(store):
    return ['--mount', f"type={store['type']},source={store['source']},target=/source,readonly"]


def manifest(docker, plan, store):
    return json.loads(docker.call(['run', '--rm', '--network', 'none', '--read-only',
        *store_args(store), '--entrypoint', 'python', plan['helper_image'], '-c', STORE_MANIFEST]))


def archive_manifest(path):
    records = []
    with tarfile.open(path, 'r:*') as archive:
        seen = set()
        for member in archive:
            name = member.name
            while name.startswith('./'):
                name = name[2:]
            if name in ('', '.'):
                name = '.'
            if name.startswith('/') or '\\' in name or '..' in name.split('/') or name in seen:
                raise ValueError('Unsafe or duplicate archive member')
            seen.add(name)
            item = dict(path=name, mode=member.mode, uid=member.uid, gid=member.gid,
                        mtime=int(member.mtime))
            if member.isdir():
                item.update(type='directory')
            elif member.issym():
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
                if (member.linkname.startswith('/') or '\\' in member.linkname
                        or resolved == '..' or resolved.startswith('../')):
                    raise ValueError('Archive symlink escapes the source store')
                item.update(type='symlink', link=member.linkname)
            elif member.isfile():
                with archive.extractfile(member) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                item.update(type='file', size=member.size, sha256=digest)
            else:
                raise ValueError('Unsupported archive member type')
            records.append(item)
    return sorted(records, key=lambda item: item['path'])


def backup_frozen(plan, destination: Path, docker=None):
    validate_plan(plan)
    docker = docker or Docker(plan.get('context', 'desktop-linux'))
    sources = assert_frozen(docker, plan)
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / 'private-plan.json', plan)
    # No environment values/credentials are printed. Private source manifest stays local.
    write_json(destination / 'source-containers.json', [{k: item[k] for k in
        ('Id', 'Name', 'Image', 'State', 'Mounts')} for item in sources])
    before = {s['name']: manifest(docker, plan, s) for s in plan['stores']}
    write_json(destination / 'source-files-before.json', before)
    for store in plan['stores']:
        assert_frozen(docker, plan)
        target = destination / (store['name'] + '.tar.gz')
        docker.call(['run', '--rm', '--network', 'none', '--read-only', *store_args(store),
                     '--entrypoint', 'tar', plan['helper_image'], '-C', '/source', '-czf', '-', '.'],
                    destination=target)
        if archive_manifest(target) != before[store['name']]:
            raise RuntimeError('Archive differs from source manifest: ' + store['name'])
    after = {s['name']: manifest(docker, plan, s) for s in plan['stores']}
    assert_frozen(docker, plan)
    if before != after:
        raise RuntimeError('A source changed during the coordinated cold backup')
    write_json(destination / 'source-files-after.json', after)
    files = []
    for path in sorted(destination.iterdir()):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append(dict(name=path.name, bytes=path.stat().st_size, sha256=digest))
    result = dict(schema='daibm.cold-backup-result.v1', files=files,
                  completed_at=datetime.now(timezone.utc).isoformat(),
                  sources_remain_frozen=True, archives_verified=True)
    # Only this final completion marker makes the backup eligible for restoration.
    write_json(destination / 'backup-manifest.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    result = backup_frozen(json.loads(args.plan.read_text(encoding='utf-8')), args.destination)
    print(json.dumps(dict(completed_at=result['completed_at'], archives_verified=True,
                          sources_remain_frozen=True)))


if __name__ == '__main__':
    main()
