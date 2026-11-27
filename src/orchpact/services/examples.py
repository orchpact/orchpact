"""Publish packaged examples explicitly; use the normal catalog and Workspace delivery."""
from ..adapters.configuration import tool_configuration_digest
from ..adapters.database import loads
from ..kernel.errors import DomainError, NotFound
from ..kernel.workflows import definition_digest
from ..scenarios import EXAMPLES


class WorkflowExamples:
    def __init__(self, service):
        self.s = service

    @staticmethod
    def get(key):
        if key not in EXAMPLES:
            raise NotFound('内置示例不存在')
        return EXAMPLES[key]

    def revision(self, c, definition):
        return c.execute('''SELECT revision FROM workflow_versions
            WHERE workflow_id=%s AND sha256=%s ORDER BY revision DESC LIMIT 1''',
            (definition['id'], definition_digest(definition))).fetchone()

    def list(self, ctx):
        self.s.workflows.admin(ctx)
        result = []
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            for key, example in EXAMPLES.items():
                definition = example.definition()
                workflow = definition['id']
                revision = self.revision(c, definition)
                installed = self.s.delivery.installed(c, ctx, workflow)
                eligible = False
                if installed:
                    asset = self.s.workflows.resolve(c, workflow, installed['workflow_revision'])
                    eligible = (asset['sha256'] == definition_digest(definition)
                                and self.ready_installation(c, ctx, example, definition, installed))
                conflict = not revision and c.execute(
                    'SELECT 1 FROM workflows WHERE id=%s', (workflow,)).fetchone() is not None
                result.append(dict(id=key, name=example.name, description=example.description,
                    highlights=example.highlights, human_review=example.human_review, workflow_id=workflow,
                    revision=revision['revision'] if revision else None,
                    installed_revision=installed['workflow_revision'] if eligible else None, conflict=conflict))
        return result

    def ready_installation(self, c, ctx, example, definition, installed):
        operation, manifest = self.installed_manifest(c, ctx, definition, installed)
        bindings = manifest['_workflow']['bindings']
        provider = example.provider(definition)
        if bindings.get('tools', {}).get('verifier') != self.tool_binding(provider):
            return False
        current = self.s.configuration.record(provider['id'], c)
        if not self.provider_matches(current, provider):
            return False
        if example.human_review:
            return self.s.policies.read(c, 'unit:'+operation['unit_id'])['policy']['independent_review']
        return True

    @staticmethod
    def installed_manifest(c, ctx, definition, installed):
        row = c.execute('SELECT manifest FROM organization_packages WHERE workspace_id=%s AND application_id=%s',
                        (ctx.workspace_id, installed['scope_id'])).fetchone()
        manifest = loads(row['manifest'])
        return manifest['operations'][next(iter(definition['operations']))], manifest

    def detail(self, ctx, key):
        self.s.workflows.admin(ctx)
        example = self.get(key)
        definition = example.definition()
        return dict(definition=definition, sha256=definition_digest(definition),
                    operation=next(iter(definition['operations'])), input=example.sample())

    def prepare(self, ctx, key):
        self.s.workflows.admin(ctx)
        example = self.get(key)
        definition = example.definition()
        with self.s.db.connect() as c:
            self.s.delivery.member(c, ctx)
            revision = self.revision(c, definition)
            if not revision and c.execute('SELECT 1 FROM workflows WHERE id=%s', (definition['id'],)).fetchone():
                raise DomainError('example_workflow_conflict', '目录已有同标识定义，请保留原定义并检查示例修订')
        provider = example.provider(definition)
        configuration = self.s.configuration.public()
        previous = next((row for row in configuration['value']['providers'] if row['id'] == provider['id']), None)
        if previous is None:
            configuration['value']['providers'].append(provider)
            self.s.configuration.save(configuration['revision'], configuration['value'], ctx.subject)
        else:
            self.check_provider(previous, provider)
        if not revision:
            try:
                revision = self.s.workflows.register(ctx, definition)
            except DomainError as error:
                if error.code != 'revision_conflict':
                    raise
                with self.s.db.connect() as c:
                    revision = self.revision(c, definition)
                if not revision:
                    raise
        return dict(revision=revision['revision'], provider_id=provider['id'])

    @staticmethod
    def provider_matches(current, expected):
        return (current and current['category'] == 'tool' and current['enabled']
                and current['pricing'] == expected['pricing']
                and tool_configuration_digest(current) == tool_configuration_digest(expected))

    def check_provider(self, current, expected):
        if not self.provider_matches(current, expected):
            raise DomainError('example_verifier_conflict', '示例校验器已停用或配置已变化，请在模型与工具中核对')

    @staticmethod
    def tool_binding(provider):
        return dict(endpoint_ref=provider['id'], implementation_version=provider['implementation_version'])

    def install(self, ctx, key, revision, bindings, expected_revision):
        self.s.workflows.admin(ctx)
        example = self.get(key)
        definition = example.definition()
        provider = example.provider(definition)
        with self.s.db.tx() as c:
            asset = self.s.workflows.resolve(c, definition['id'], revision)
            if asset['sha256'] != definition_digest(definition):
                raise DomainError('example_workflow_conflict', '请选择对应的内置示例修订')
            if bindings.get('tools') != {'verifier': self.tool_binding(provider)}:
                raise ValueError('内置示例需要绑定对应的本地校验器')
            self.check_provider(self.s.configuration.record(provider['id'], c), provider)
            result = self.s.delivery.install(ctx, definition['id'], revision, bindings, expected_revision, c=c)
            if example.human_review:
                installed = self.s.delivery.installed(c, ctx, definition['id'])
                operation, _ = self.installed_manifest(c, ctx, definition, installed)
                scope = 'unit:' + operation['unit_id']
                rule = self.s.policies.read(c, scope)
                if not rule['policy']['independent_review']:
                    self.s.policies.save(c, scope, rule['revision'], dict(rule['policy'], independent_review=True))
            return result
