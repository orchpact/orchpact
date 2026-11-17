"""Initialize and operate Orchpact instances."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil

from .config import Settings


def initialize(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / 'config'
    config.mkdir(exist_ok=True, mode=0o700)
    defaults = {
        'models.toml': '# Optional until the first run. Credentials are environment references.\n[models.reasoning]\nlabel = "Text"\nkind = "text"\nmodel = "configure-your-model"\nbase_url = "https://api.deepseek.com"\nkey_env = "DEEPSEEK_API_KEY"\njson_mode = true\nenabled = "auto"\npriority = 10\n',
        'frontends.toml': '# Optional app mounts; no sibling directory dependencies.\n',
        'execution.toml': '[execution]\nworkers = 4\nmax_tasks_per_project = 1\n',
        'credits.toml': '[credits]\ntokens_per_point = 1000\nsearch_points = 10\n',
        'security.toml': '# Public deployments require an explicit HTTPS origin.\n[security]\n',
    }
    for name, text in defaults.items():
        path = config / name
        if not path.exists():
            with path.open('x') as stream:
                stream.write(text)
    env = root / '.env'
    if not env.exists():
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('ORCHPACT_API_TOKEN=' + secrets.token_urlsafe(48) + '\nORCHPACT_DATABASE_URL=postgresql://orchpact:configure-password@127.0.0.1:5432/orchpact\nDEEPSEEK_API_KEY=\n')
    return root


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Orchpact reusable workflow kernel')
    parser.add_argument('--root', default='.', help='实例根目录')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8780)
    commands.add_parser('check')
    workflow=commands.add_parser('workflow-check', help='Validate a portable definition without instance configuration')
    workflow.add_argument('definition',type=Path)
    backup = commands.add_parser('backup')
    backup.add_argument('destination')
    args = parser.parse_args()
    try:
        if args.command == 'workflow-check':
            from .kernel.workflows import validate_definition,definition_digest
            value=validate_definition(json.loads(args.definition.read_text()))
            print('工作流定义校验通过：'+value['id']+' / '+value['version'])
            print('SHA-256 '+definition_digest(value))
            return
        if args.command == 'init':
            root = initialize(args.root)
            print('实例配置已初始化：' + str(root))
            print('管理凭据仅保存在实例 .env；请配置 ORCHPACT_DATABASE_URL，模型按需配置。')
            return
        settings = Settings.load(args.root, getattr(args, 'host', '127.0.0.1'), getattr(args, 'port', 8780))
        if args.command == 'check':
            for profile in settings.catalog():
                print(profile['id'], profile['kind'], ','.join(profile['capabilities']))
            print('PostgreSQL 连接配置通过；' + str(len(settings.catalog())) + ' 个可用模型；执行前仍检查权限、余额与能力。')
            return
        if args.command == 'backup':
            from filelock import FileLock
            destination = Path(args.destination).resolve()
            if destination.exists() or destination.is_relative_to(settings.root):
                raise ValueError('备份目标须是实例外不存在的目录')
            from .adapters.database import Database
            if not settings.data.is_dir():raise ValueError('实例尚未启动，没有可备份的文件数据目录')
            with FileLock(str(settings.data/'process.lock'),timeout=0):
                db=Database(settings.database_url,settings.data/'backups')
                db.acquire_owner()
                try:
                    db.verify_file_store()
                    destination.mkdir(mode=0o700)
                    db.backup('database',directory=destination)
                    shutil.copytree(settings.data,destination/'var',ignore=shutil.ignore_patterns('*.lock'))
                    shutil.copytree(settings.root/'config',destination/'config')
                finally:db.close()
            print('停止实例备份完成：PostgreSQL dump、本地文件和配置；环境凭据须另行安全备份。')
            return
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1,
                    timeout_graceful_shutdown=200)
    except (ValueError, KeyError, FileNotFoundError,RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
