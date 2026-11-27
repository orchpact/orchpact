"""Start a local Orchpact instance with Docker Compose."""
import argparse
from pathlib import Path
import subprocess
import sys

from configure import configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', default='admin', help='新实例的管理员账号名（默认 admin）')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env_file = root/'docker/.env'
    compose = ['docker', 'compose', '--env-file', str(env_file)]

    def run(*arguments, capture=False):
        return subprocess.run([*compose, *arguments], cwd=root, check=True, text=True,
                              stdout=subprocess.PIPE if capture else None).stdout

    try:
        try:
            configure(env_file)
        except FileExistsError:
            pass
        run('config', '--quiet')
        if not run('ps', '--status', 'running', '--quiet', 'orchpact', capture=True).strip():
            run('up', '-d', '--wait', 'postgres')
            run('build', 'orchpact')
            run('run', '--rm', '--no-deps', 'orchpact', 'setup-admin', args.admin, '--if-missing')
        run('up', '-d', '--wait')
        addresses = run('port', 'orchpact', '8780', capture=True).split()
        if not addresses:
            raise OSError('未找到 Orchpact 的本机端口')
        print(f'启动完成，请打开 http://{addresses[0]}/ 并登录。')
        return 0
    except FileNotFoundError as error:
        print(f'无法启动：{error.filename} 不存在。请检查 Docker 是否已安装。', file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'启动中止，请检查上方的 Docker 或账号初始化提示（{getattr(error, "returncode", error)}）。',
              file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
