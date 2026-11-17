"""Pure path, file-count and delivery-size constraints."""
from pathlib import PurePosixPath
import re

MAX_BYTES=10*1024*1024
MAX_FILES=20

def text_name(text):
    return 'result.md' if re.search(r'(?m)^#{1,6} |^```|^\|.*\||^[-*] |\*\*[^*]+\*\*|\[[^\]]+\]\(https?://',text) else 'result.txt'

def encode_files(files):
    seen=set(); encoded=[]; size=0
    if not files or len(files)>MAX_FILES: raise ValueError('交付需要1–20个文件')
    for file in files:
        name=file['path']; path=PurePosixPath(name)
        if not name or len(name)>160 or path.is_absolute() or '\\' in name or ':' in name or '\x00' in name or any(p in ('..','.','') for p in name.split('/')) or name.endswith('/'):
            raise ValueError('文件路径不安全')
        if name.casefold() in seen: raise ValueError('交付文件名重复')
        seen.add(name.casefold())
        data=file.get('data')
        if data is None: data=file['content'].encode('utf-8')
        size+=len(data)
        if size>MAX_BYTES: raise ValueError('单次交付不能超过10MB')
        encoded.append((name,data))
    return encoded
