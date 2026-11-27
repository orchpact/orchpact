"""Standalone credential creation and installed distribution identity."""
import importlib.metadata
import os
from pathlib import Path
import runpy
import tempfile
import unittest
import orchpact

class DistributionTests(unittest.TestCase):
    def test_module_version_matches_installed_distribution(self):
        self.assertEqual(orchpact.__version__,importlib.metadata.version('orchpact'))

    def test_compose_credentials_are_private_unique_and_never_overwritten(self):
        script=Path(__file__).resolve().parents[1]/'docker/configure.py'
        configure=runpy.run_path(str(script))['configure']
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'.env';configure(target)
            original=target.read_bytes();values=dict(line.split('=',1) for line in original.decode().splitlines())
            secrets=[values[key] for key in ('ORCHPACT_DB_PASSWORD','POSTGRES_ADMIN_PASSWORD')]
            self.assertEqual(len(set(secrets)),2)
            self.assertTrue(all(len(value)>=48 for value in secrets))
            if os.name=='posix':self.assertEqual(target.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):configure(target)
            self.assertEqual(target.read_bytes(),original)

    def test_environment_configuration_is_private_and_process_values_take_precedence(self):
        from unittest.mock import patch
        from orchpact.cli import initialize
        from orchpact.config import Settings
        from orchpact.testing import database_url_for
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);url=database_url_for(root/'var');initialize(root)
            local='removed-fixture-token-'*4
            (root/'.env').write_text('ORCHPACT_API_TOKEN='+local+'\nORCHPACT_DATABASE_URL='+url+'\n')
            with patch.dict(os.environ,{},clear=True):
                settings=Settings.load(root)
                self.assertEqual(settings.database_url,url)
                self.assertNotIn('ORCHPACT_API_TOKEN',os.environ)
                self.assertNotIn('ORCHPACT_DATABASE_URL',os.environ)
            (root/'.env').write_text('ORCHPACT_DATABASE_URL=ignored-by-process\n')
            with patch.dict(os.environ,{'ORCHPACT_DATABASE_URL':url},clear=True):
                settings=Settings.load(root)
                self.assertEqual(settings.database_url,url)
