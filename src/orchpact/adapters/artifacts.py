"""Filesystem delivery: atomic writes, immutable UUID objects and bounded ZIP packages."""
import codecs
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile
from .database import ident, now, dumps

from ..kernel.files import MAX_BYTES, MAX_FILES, text_name, encode_files

class Artifacts:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)

    def publish(self,c,task,attempt,files):
        encoded=encode_files(files)
        if shutil.disk_usage(self.root).free<150*1024*1024:raise ValueError('交付存储可用空间不足150MB，请先处理磁盘')
        used=c.execute('SELECT COALESCE(SUM(bytes),0) FROM artifacts WHERE project_id=%s',(task['project_id'],)).fetchone()[0]
        if used+sum(len(b) for _,b in encoded)>500*1024*1024:raise ValueError('项目文件交付存储已达500MB上限')
        manifest=[dict(path=n,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for n,b in encoded]
        if len(encoded)>1:
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
                for name,data in encoded:z.writestr(name,data)
            name,data,media='deliverables.zip',stream.getvalue(),'application/zip'
        else:
            name,data=encoded[0]
            media={'.json':'application/json','.md':'text/markdown','.txt':'text/plain','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(Path(name).suffix.lower(),'application/octet-stream')
        aid=ident(); dest=self.root/aid
        tmp=self.root/(aid+'.tmp')
        try:
            with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            tmp.replace(dest)
            c.execute('INSERT INTO artifacts VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (aid,task['project_id'],task['id'],attempt,name,media,len(data),hashlib.sha256(data).hexdigest(),aid,dumps(manifest),now()))
        except Exception:
            tmp.unlink(missing_ok=True);dest.unlink(missing_ok=True);raise
        return aid

    def excerpts(self, artifact, limit=18000):
        """Enumerate every file, even when the bounded text budget is exhausted."""
        files = []
        remaining = limit
        for item in self.read_files(artifact):
            data = item['data']
            entry = dict(path=item['path'], bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            try:
                content = data.decode('utf-8')
            except UnicodeDecodeError:
                entry.update(content='', truncated=True, omitted=True, reason='binary_content')
            else:
                shown = content[:max(0, remaining)]
                entry.update(content=shown, truncated=len(shown) < len(content),
                             omitted=bool(content) and not shown,
                             reason='context_limit' if len(shown) < len(content) else None)
                remaining -= len(shown)
            files.append(entry)
        return dict(artifact_id=artifact['id'], name=artifact['name'], sha256=artifact['sha256'],
                    files=files, complete=all(not f['truncated'] for f in files),
                    note='文件清单完整；truncated/omitted 表示原文未完整提供，不能声称完整核验。')

    def read_files(self,artifact):
        """Read only platform-published artifacts, bounded before decompression."""
        path=self.path(artifact)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']:raise ValueError('交付哈希不匹配')
        if artifact['media_type']=='application/zip':
            with zipfile.ZipFile(path) as archive:
                entries=archive.infolist()
                if len(entries)>MAX_FILES or sum(e.file_size for e in entries)>MAX_BYTES:raise ValueError('引用交付超过文件数或大小限制')
                files=[dict(path=e.filename,data=archive.read(e)) for e in entries]
        else:
            if path.stat().st_size>MAX_BYTES:raise ValueError('引用文件过大')
            files=[dict(path=artifact['name'],data=path.read_bytes())]
        encode_files(files)
        return files

    def path(self,artifact):
        path=(self.root/artifact['path']).resolve()
        if path.parent!=self.root or not path.is_file():raise FileNotFoundError('交付文件不存在')
        return path

    def reconcile(self,known_paths,older_than=86400):
        """Remove old temporary and orphaned objects without touching live deliveries."""
        import time
        removed=0;cutoff=time.time()-older_than
        for path in self.root.iterdir():
            if not path.is_file() or path.name in known_paths:continue
            try:
                if path.stat().st_mtime>=cutoff:continue
                path.unlink();removed+=1
            except OSError:pass
        return removed
