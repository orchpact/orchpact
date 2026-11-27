"""Create local Compose credentials once, without displaying them."""
import argparse
import os
from pathlib import Path
import secrets


def configure(destination):
    path=Path(destination)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:
        for key in ('POSTGRES_ADMIN_PASSWORD','ORCHPACT_DB_PASSWORD'):
            stream.write(key+'='+secrets.token_urlsafe(48)+'\n')
        stream.write('ORCHPACT_PORT=8780\n')
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).with_name('.env'))
    args=parser.parse_args()
    try:
        configure(args.output)
    except FileExistsError:parser.error('目标已存在；保留已有凭据，不覆盖')
    print('本地 Compose 配置已创建（权限 600）；凭据未显示。')
