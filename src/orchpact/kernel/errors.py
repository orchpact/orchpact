"""Errors crossing domain, application and transport boundaries."""


class DomainError(ValueError):
    def __init__(self, code, message, status=409):
        self.code = code
        self.status = status
        super().__init__(message)


class Forbidden(DomainError):
    def __init__(self, message="当前主体未获授权"):
        super().__init__("forbidden", message, 403)


class NotFound(DomainError):
    def __init__(self, message="资源不存在或无权访问"):
        super().__init__("not_found", message, 404)
