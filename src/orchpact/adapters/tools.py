"""Static tool transports with pinned, policy-checked HTTPS connections."""
from dataclasses import dataclass
from http.client import HTTPSConnection
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


class ToolTransportError(RuntimeError):
    def __init__(self, code, *, definite=False):
        super().__init__(code)
        self.code = code
        self.definite = definite


@dataclass(frozen=True)
class AdapterContext:
    tool_call_id: str
    idempotency_key: str
    definition: dict
    binding: dict
    provider: dict
    arguments: dict
    credential: str


class PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, host, port, addresses, timeout, context):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._addresses = addresses

    def connect(self):
        errors = []
        for address in self._addresses:
            try:
                raw = socket.create_connection((address, self.port), self.timeout)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                errors.append(exc)
        raise ToolTransportError('connect_failed') from (errors[-1] if errors else None)


class HTTPAdapter:
    supports_lookup = False

    def __init__(self, resolver=None, ssl_context=None, max_redirects=3):
        self.resolver = resolver or socket.getaddrinfo
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.max_redirects = max_redirects

    def _url(self, value, initial_host, policy):
        try:
            parsed = urlsplit(value)
            port = parsed.port or 443
            host = (parsed.hostname or '').rstrip('.').lower()
            if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment:
                raise ValueError()
            if '\r' in value or '\n' in value or any(ord(char) < 32 for char in value):
                raise ValueError()
        except (ValueError, AttributeError):
            raise ToolTransportError('target_rejected', definite=True) from None
        allowed = {initial_host, *policy['network']['allowed_hosts']}
        if host not in allowed or port not in policy['network']['allowed_ports']:
            raise ToolTransportError('target_not_allowed', definite=True)
        return parsed, host, port

    def _addresses(self, host, port, policy):
        try:
            rows = self.resolver(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(row[4][0] for row in rows))
            if not addresses or len(addresses) > 16:
                raise ValueError()
        except (OSError, ValueError, TypeError):
            raise ToolTransportError('dns_failed', definite=True) from None
        allow_private = policy['network']['allow_private']
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                raise ToolTransportError('dns_failed', definite=True) from None
            unsafe = address.is_unspecified or address.is_multicast or address.is_reserved
            if unsafe or not (allow_private or address.is_global):
                raise ToolTransportError('private_target_rejected', definite=True)
        return addresses

    def _read(self, response, connection, maximum, deadline):
        declared = response.getheader('Content-Length')
        if declared is not None:
            try:
                if int(declared) > maximum:
                    raise ToolTransportError('response_too_large')
            except ValueError:
                raise ToolTransportError('invalid_content_length') from None
        chunks = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolTransportError('deadline_exceeded')
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read(min(65536, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise ToolTransportError('response_too_large')
        return b''.join(chunks)

    def invoke(self, context):
        provider = context.provider
        policy = provider['tool_policy']
        current = provider['base_url']
        initial_host = (urlsplit(current).hostname or '').rstrip('.').lower()
        initial_port = urlsplit(current).port or 443
        deadline = time.monotonic() + context.definition['timeout_ms'] / 1000
        method = 'POST'
        body = json.dumps(dict(tool_call_id=context.tool_call_id,idempotency_key=context.idempotency_key,
            interface=context.definition['interface'],version=context.definition['version'],arguments=context.arguments),
            ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        for redirect in range(self.max_redirects + 1):
            parsed, host, port = self._url(current, initial_host, policy)
            addresses = self._addresses(host, port, policy)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolTransportError('deadline_exceeded')
            connection = PinnedHTTPSConnection(host, port, addresses, remaining, self.ssl_context)
            target = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            headers = {'Accept':'application/json','Content-Type':'application/json','Host':host if port == 443 else host + ':' + str(port),
                'X-Orchpact-Tool-Call':context.tool_call_id,'Idempotency-Key':context.idempotency_key}
            if context.credential and (host,port) == (initial_host,initial_port):
                headers['Authorization'] = 'Bearer ' + context.credential
            try:
                payload = body if method == 'POST' else None
                if payload is not None:
                    headers['Content-Length'] = str(len(payload))
                connection.request(method, target, body=payload, headers=headers)
                response = connection.getresponse()
                if connection.sock:
                    connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
                if response.status in (301,302,303,307,308):
                    location = response.getheader('Location')
                    if not location or redirect >= self.max_redirects:
                        raise ToolTransportError('redirect_rejected', definite=context.definition['effect'] != 'external_write')
                    if context.definition['effect'] == 'external_write':
                        raise ToolTransportError('external_write_redirect_unknown')
                    current = urljoin(current, location)
                    if response.status in (301,302,303):
                        method = 'GET'
                    continue
                raw = self._read(response, connection, context.definition['max_output_bytes'], deadline)
                if not 200 <= response.status < 300:
                    raise ToolTransportError('http_' + str(response.status), definite=400 <= response.status < 500)
                try:
                    return json.loads(raw)
                except (UnicodeDecodeError,json.JSONDecodeError):
                    raise ToolTransportError('invalid_json') from None
            except ToolTransportError:
                raise
            except (TimeoutError,socket.timeout):
                raise ToolTransportError('timeout') from None
            except (OSError,ssl.SSLError):
                raise ToolTransportError('connection_failed') from None
            finally:
                connection.close()
        raise ToolTransportError('redirect_rejected')


class BuiltinAdapter:
    def __init__(self, implementations=None):
        self.implementations = dict(implementations or {})

    def _implementation(self, context):
        implementation = self.implementations.get(context.definition['interface'])
        if implementation is None:
            raise ToolTransportError('builtin_not_registered', definite=True)
        return implementation

    def invoke(self, context):
        return self._implementation(context).invoke(context)

    def lookup(self, context):
        implementation = self._implementation(context)
        lookup = getattr(implementation, 'lookup', None)
        if lookup is None:
            raise ToolTransportError('lookup_unsupported', definite=True)
        return lookup(context)


class ToolRegistry:
    def __init__(self, http=None, builtins=None):
        self.adapters = {'http':http or HTTPAdapter(),'builtin':BuiltinAdapter(builtins)}

    def resolve(self, binding):
        adapter = self.adapters.get(binding['type'])
        if adapter is None:
            raise ToolTransportError('adapter_not_supported', definite=True)
        return adapter
