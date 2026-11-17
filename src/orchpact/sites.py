"""Host-bound product surface. The reverse proxy preserves Host; no client identity headers are trusted."""
import re
from pathlib import Path
from fastapi import HTTPException
from .access import principal


ACCOUNT_PAGE=Path(__file__).parent/'static/account/index.html'


def account_page_html(*,product_directory=None):
    html=ACCOUNT_PAGE.read_text(encoding='utf-8')
    if product_directory is not None:
        html=html.replace('<body>','<body data-product-root="true">')
        html=re.sub(r'<details><summary>管理员登录</summary>.*?</details>','',html,flags=re.S)
        root=product_directory.resolve()
        icon=(root/'favicon.svg').resolve()
        if icon.is_relative_to(root) and icon.is_file():
            html=html.replace('href="/static/account/favicon.svg"','href="/favicon.svg"')
    return html


def product(settings,request):
    return settings.product_for_host(request.url.hostname)


def audience(settings,request):
    if not settings.products:return ''
    return settings.origin_for_host(request.url.hostname) or 'internal'


from .interfaces.surface import product_path


def frontend_file(settings,app_id,path):
    root=(settings.root/settings.frontends[app_id]['directory']).resolve()
    relative=path.lstrip('/') or 'index.html'
    if relative=='integration.json':raise HTTPException(404,'资源不存在')
    file=(root/relative).resolve()
    if not file.is_relative_to(root) or not file.is_file():raise HTTPException(404,'页面不存在')
    return file
