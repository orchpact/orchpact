"""Storage lifecycle, response limits, cleanup and shutdown bounds."""
from pathlib import Path
from types import SimpleNamespace
import threading
import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
from unittest.mock import Mock,patch
from runtime_fixtures import KernelFixture
from orchpact.provider_http import bounded_json
from orchpact.services.runner import Runner

class InfrastructureTests(KernelFixture):
    def test_response_limit(self):
        response=httpx.Response(200,request=httpx.Request("POST","https://invalid"),content=b"{}",headers={"Content-Length":"100"})
        with self.assertRaises(ValueError):bounded_json(response,maximum=10)

    def test_response_limit(self):
        response=httpx.Response(200,request=httpx.Request("POST","https://invalid"),content=b"{}",headers={"Content-Length":"100"})
        with self.assertRaises(ValueError):bounded_json(response,maximum=10)

    def test_named_migration_is_backed_up_and_repeatable(self):
        db=self.s.db
        self.assertTrue(db.migrate('0002_test_table','CREATE TABLE migration_probe(id TEXT PRIMARY KEY)'))
        self.assertFalse(db.migrate('0002_test_table','CREATE TABLE should_not_run(id TEXT)'))
        self.assertTrue((db.backup_directory/'0002_test_table.dump').exists())
        with db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='0002_test_table'").fetchone()[0],1)
        with self.assertRaises(psycopg.Error):
            db.migrate('0003_retry_probe',['CREATE TABLE retry_probe(id TEXT)','SELECT 1/0'])
        original=(db.backup_directory/'0003_retry_probe.dump').read_bytes()
        with db.connect() as c:self.assertFalse(db.has_table(c,'retry_probe'))
        self.assertTrue(db.migrate('0003_retry_probe','CREATE TABLE retry_probe(id TEXT)'))
        self.assertEqual((db.backup_directory/'0003_retry_probe.dump').read_bytes(),original)
        self.assertEqual(len(list(db.backup_directory.glob('0003_retry_probe*.dump'))),2)
    def test_connection_context_closes_after_commit(self):
        import psycopg
        connection=self.s.db.connect()
        with connection as c:c.execute('SELECT 1')
        with self.assertRaises(psycopg.OperationalError):connection.execute('SELECT 1')
    def test_runner_periodically_reconciles_files(self):
        runner=Runner(self.s);runner.executor=SimpleNamespace(providers=None)
        with patch('orchpact.services.runner.time.monotonic',return_value=10),patch.object(self.s,'janitor') as clean:
            self.assertFalse(runner.step());clean.assert_called_once_with()
            self.assertFalse(runner.step());clean.assert_called_once_with()
    def test_shutdown_deadline_retains_ownership(self):
        thread=Mock();thread.is_alive.return_value=True;runner=Runner(self.s);runner.threads=[thread]
        with self.assertRaises(TimeoutError):runner.stop(timeout=0)
        self.assertTrue(runner.stop_event.is_set());self.assertTrue(self.app.state.instance.lock.is_locked)

    def test_postgresql_dump_restores_schema_data_and_immutable_payer(self):
        import os,subprocess
        from orchpact.testing import database_url_for
        from orchpact.adapters.database import Database
        run=self.invoke();self.finish()
        backup=self.s.db.backup('restore-probe')
        restored_url=database_url_for(self.root/'restore')
        options={option.keyword.decode():option.envvar.decode() for option in psycopg.pq.Conninfo.get_defaults() if option.envvar}
        env=dict(os.environ)
        for key,value in conninfo_to_dict(restored_url).items():env[options[key]]=value
        binary=Path(os.environ['ORCHPACT_PG_DUMP']).with_name('pg_restore')
        result=subprocess.run([str(binary),'--dbname',conninfo_to_dict(restored_url)['dbname'],'--no-owner','--no-acl',str(backup)],env=env,capture_output=True)
        self.assertEqual(result.returncode,0,'pg_restore failed')
        with Database(restored_url).connect() as c:
            self.assertEqual(c.execute('SELECT created_by FROM projects WHERE id=%s',(run['id'],)).fetchone()[0],self.user)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT value FROM instance_metadata WHERE key='format'").fetchone()[0],'orchpact-postgresql-v1')

    def test_postgresql_connections_close_without_idle_transactions(self):
        for _ in range(30):
            with self.s.db.connect() as c:c.execute('SELECT 1')
        with self.s.db.connect() as c:
            count=c.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend'").fetchone()[0]
            self.assertEqual(count,2) # Owner session and this observation only.
            self.assertEqual(c.execute('SHOW synchronous_commit').fetchone()[0],'on')
            self.assertEqual(c.execute('SHOW fsync').fetchone()[0],'on')

    def test_backup_pair_validation_rejects_wrong_files(self):
        from orchpact.adapters.database import Database
        self.s.db.verify_file_store()
        mismatch=Database(self.settings.database_url,self.root/'unrelated/backups')
        with self.assertRaisesRegex(ValueError,'不匹配'):mismatch.verify_file_store()

    def test_fixture_creates_nested_root_and_ignores_business_connection(self):
        from orchpact.testing import database_url_for
        from orchpact.adapters.database import Database
        root=self.root/'nested'/'fixture'
        with patch.dict('os.environ',ORCHPACT_DATABASE_URL='postgresql://unreachable.invalid/business'):
            url=database_url_for(root/'var')
            self.assertEqual(database_url_for(root/'var'),url)
        self.assertEqual((root/'.env').stat().st_mode&0o777,0o600)
        with Database(url).connect() as c:self.assertEqual(c.execute('SELECT 1').fetchone()[0],1)
