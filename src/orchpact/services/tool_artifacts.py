"""Controlled tool artifact decoding and bounded media inspection."""
import base64
from hashlib import sha256
import io
from pathlib import PurePosixPath
import stat
import zipfile

from ..kernel.contracts import read_json_delivery
from ..kernel.files import MAX_BYTES, MAX_FILES, encode_files

OOXML={
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':('word/document.xml','.docx'),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':('xl/workbook.xml','.xlsx'),
    'application/vnd.openxmlformats-officedocument.presentationml.presentation':('ppt/presentation.xml','.pptx'),
}
EXTENSIONS={
    'application/json':{'.json'},'text/plain':{'.txt'},'text/markdown':{'.md','.markdown'},
    'image/png':{'.png'},'image/jpeg':{'.jpg','.jpeg'},'image/webp':{'.webp'},
    'application/pdf':{'.pdf'},'application/zip':{'.zip'},
}


def check_zip(data,media):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)>256:raise ValueError('ZIP 条目数量超过限制')
            total=0
            for entry in entries:
                name=entry.filename;path=PurePosixPath(name)
                mode=(entry.external_attr>>16)&0o170000
                if entry.flag_bits&1 or mode==stat.S_IFLNK or path.is_absolute() or '\\' in name or ':' in name or '\x00' in name or any(part in ('..','.') for part in name.split('/')):
                    raise ValueError('ZIP 包含加密、链接或不安全路径')
                total+=entry.file_size
                if total>32*1024*1024:raise ValueError('ZIP 解压大小超过限制')
                if entry.file_size>1024*1024 and entry.file_size>max(1024,entry.compress_size*200):
                    raise ValueError('ZIP 压缩比异常')
                if name.lower().endswith('.zip'):raise ValueError('不支持嵌套 ZIP')
            if media=='application/zip' and (len(entries)>MAX_FILES or total>MAX_BYTES):
                raise ValueError('ZIP 交付超过 Artifact 文件数或大小限制')
            if media in OOXML:
                required,_=OOXML[media]
                names=set(archive.namelist())
                if '[Content_Types].xml' not in names or required not in names:raise ValueError('OOXML 缺少必需主文档')
                info=archive.getinfo('[Content_Types].xml')
                if info.file_size>1024*1024:raise ValueError('OOXML 内容类型声明过大')
                if media.encode() not in archive.read(info):raise ValueError('OOXML 内容类型与声明不符')
    except zipfile.BadZipFile:
        raise ValueError('不是有效 ZIP/OOXML 文件') from None


def inspect_media(data,media,path):
    if not data:raise ValueError('Artifact 文件不能为空')
    suffix=PurePosixPath(path).suffix.lower()
    if media in EXTENSIONS and suffix not in EXTENSIONS[media]:raise ValueError('Artifact 扩展名与媒体类型不符')
    if media in OOXML and suffix!=OOXML[media][1]:raise ValueError('OOXML 扩展名与媒体类型不符')
    if media=='application/json':read_json_delivery(data)
    elif media in ('text/plain','text/markdown'):
        try:data.decode('utf-8')
        except UnicodeDecodeError:raise ValueError('文本 Artifact 必须为 UTF-8') from None
    elif media in ('image/png','image/jpeg','image/webp'):
        from ..image_providers import image_format
        if image_format(data)[1]!=media:raise ValueError('图片内容与媒体类型不符')
    elif media=='application/pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:raise ValueError('PDF 内容结构无效')
    elif media=='application/zip' or media in OOXML:check_zip(data,media)
    elif media!='application/octet-stream':raise ValueError('暂不支持此媒体类型的内容识别')


class ToolArtifacts:
    def import_result(self,node,definition,result):
        declared=node.get('artifact_outputs',{});raw=result.get('artifacts',{})
        if not isinstance(raw,dict) or set(raw)!=set(declared):raise ValueError('工具 Artifact 文件集与节点契约不符')
        contract=definition.get('artifact_contract')
        if declared and not contract:raise ValueError('工具缺少 Artifact 契约')
        if declared and len(declared)>contract['max_files']:raise ValueError('工具 Artifact 文件数超过限制')
        files=[];total=0
        for path,expected in declared.items():
            value=raw[path]
            if not isinstance(value,dict) or set(value)!={'data_base64','sha256','media_type'}:raise ValueError('工具 Artifact 条目字段无效')
            try:data=base64.b64decode(value['data_base64'],validate=True)
            except Exception:raise ValueError('工具 Artifact 不是有效 base64') from None
            if len(data)>expected['max_bytes']:raise ValueError('工具 Artifact 超过节点大小限制')
            total+=len(data)
            if total>MAX_BYTES or total>contract['max_bytes']:raise ValueError('工具 Artifact 合计大小超过限制')
            if value['media_type']!=expected['media_type'] or value['media_type'] not in contract['media_types']:
                raise ValueError('工具 Artifact 媒体类型与声明不符')
            if sha256(data).hexdigest()!=value['sha256']:raise ValueError('工具 Artifact 摘要不匹配')
            inspect_media(data,expected['media_type'],path)
            files.append(dict(path=path,data=data))
        encode_files(files or [dict(path='placeholder.json',content='{}')])
        return files
