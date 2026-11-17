"""Explicit API surfaces, shared by the HTTP host boundary and app gateways."""
import re

def product_path(app, path, method):
    rules = [
        (r'/api/v1/auth/(login|register)', {'POST'}),
        (r'/api/v1/auth/password', {'PUT'}),
        (r'/api/v1/auth/(me|credits)', {'GET'}),
        (r'/api/v1/session', {'DELETE'}),
        (r'/api/v1/(models|image-models)', {'GET'}),
        (r'/api/v1/workspaces', {'GET', 'POST'}),
        (r'/api/v1/workspaces/join', {'POST'}),
        (r'/api/v1/workspaces/[a-zA-Z0-9_-]+', {'PATCH'}),
        (r'/api/v1/workspaces/[a-zA-Z0-9_-]+/members', {'GET'}),
        (r'/api/v1/workspaces/[a-zA-Z0-9_-]+/members/[a-zA-Z0-9_-]+', {'DELETE'}),
        (r'/api/v1/workspaces/[a-zA-Z0-9_-]+/invitations', {'GET', 'POST'}),
        (r'/api/v1/workspaces/[a-zA-Z0-9_-]+/invitations/[a-zA-Z0-9_-]+', {'DELETE'}),
        (r'/api/v1/apps/' + re.escape(app), {'GET'}),
        (r'/api/v1/apps/' + re.escape(app) + r'/documents/[a-zA-Z0-9_-]+', {'GET', 'PUT'}),
        (r'/api/v1/apps/' + re.escape(app) + r'/runs', {'GET', 'POST'}),
        (r'/api/v1/apps/' + re.escape(app) + r'/runs-page', {'GET'}),
        (r'/api/v1/apps/' + re.escape(app) + r'/runs/[a-zA-Z0-9_-]+', {'GET'}),
        (r'/api/v1/runs/[a-zA-Z0-9_-]+/(usage|requests|events)', {'GET'}),
        (r'/api/v1/runs/[a-zA-Z0-9_-]+/(cancel|retry|commands)', {'POST'}),
        (r'/api/v1/work-items/[a-zA-Z0-9_-]+/retry', {'POST'}),
        (r'/api/v1/work-items/[a-zA-Z0-9_-]+/attempts', {'GET'}),
        (r'/api/v1/questions/[a-zA-Z0-9_-]+/answers', {'POST'}),
        (r'/api/v1/artifacts/[a-zA-Z0-9_-]+/(download|preview)', {'GET'}),
        (r'/api/v1/requests/[a-zA-Z0-9_-]+', {'GET'}),
    ]
    return any(method in methods and re.fullmatch(pattern, path) for pattern, methods in rules)
