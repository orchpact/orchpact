"""Transactional, privacy-bounded audit records for administrative workflows."""
import re
from .db import dumps, ident, loads, now


SENSITIVE = ('password', 'token', 'secret', 'credential', 'content', 'note', 'snapshot', 'fingerprint')


def _summary(value):
    """Keep only small operational fields; never persist secrets or private prose."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if any(word in key.lower() for word in SENSITIVE):
            continue
        if isinstance(item, (bool, int, float)) or item is None:
            result[key] = item
        elif isinstance(item, str):
            result[key] = item[:200]
        elif isinstance(item, (list, tuple)):
            result[key + '_count'] = len(item)
    return result


class Audit:
    def __init__(self, db):
        self.db = db

    def record(self, c, actor, action, target_type, target_id, *, before=None, after=None,
               workspace_id=None, request_id=None):
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,100}', action):
            raise ValueError('审计动作格式无效')
        actor_id = getattr(actor, 'user_id', None) or ('platform_admin' if getattr(actor, 'admin', False) else str(actor or 'system'))
        workspace_id = workspace_id if workspace_id is not None else getattr(actor, 'workspace_id', None)
        request_id = str(request_id)[:120] if request_id else None
        key = ident()
        c.execute('''INSERT INTO audit_log(id,actor_id,workspace_id,action,target_type,target_id,
            before_summary,after_summary,request_id,created) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (key, actor_id, workspace_id, action, target_type[:60], str(target_id)[:120],
             dumps(_summary(before)), dumps(_summary(after)), request_id, now()))
        return key

    def page(self, limit=50, cursor=None, action=None, workspace_id=None, actor_id=None):
        if not 1 <= limit <= 100:
            raise ValueError('分页大小须为1–100')
        where, parameters = [], []
        if cursor:
            with self.db.connect() as c:
                old = c.execute('SELECT created FROM audit_log WHERE id=%s', (cursor,)).fetchone()
            if not old:
                raise ValueError('审计游标无效')
            where.append('(created<%s OR (created=%s AND id<%s))')
            parameters.extend([old['created'], old['created'], cursor])
        for column, value in (('action', action), ('workspace_id', workspace_id), ('actor_id', actor_id)):
            if value:
                where.append(column + '=%s')
                parameters.append(value)
        parameters.append(limit + 1)
        with self.db.connect() as c:
            rows = c.execute('SELECT * FROM audit_log' + (' WHERE ' + ' AND '.join(where) if where else '') +
                             ' ORDER BY created DESC,id DESC LIMIT %s', parameters).fetchall()
        items = [dict(r, before=loads(r['before_summary']), after=loads(r['after_summary'])) for r in rows[:limit]]
        for item in items:
            item.pop('before_summary', None)
            item.pop('after_summary', None)
        return dict(items=items, next_cursor=rows[limit - 1]['id'] if len(rows) > limit else None)
