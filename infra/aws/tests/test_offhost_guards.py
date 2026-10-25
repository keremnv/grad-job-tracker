"""Reject unsafe off-host test requests before starting any Docker/database work."""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('recover_offhost.py')
spec = importlib.util.spec_from_file_location('recover_offhost', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OffhostGuardsTests(unittest.TestCase):
    def inputs(self, directory):
        import hashlib
        base = Path(directory)
        archive = base / 'archive.dump'
        archive.write_bytes(b'PGDMP-synthetic-guard-test')
        manifest = base / 'manifest.json'
        manifest.write_text(json.dumps({'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'fingerprint': {'tables': {k: {'count': n} for k, n in
                [('postings', 2), ('posting_versions', 3), ('applications', 1), ('owner_audit', 4), ('collection_runs', 0)]}}}))
        return archive, manifest, base / 'output'

    def test_corrupt_archive_refused_before_docker(self):
        with TemporaryDirectory() as directory:
            archive, manifest, output = self.inputs(directory)
            archive.write_bytes(b'corrupt')
            with patch.object(module.subprocess, 'check_output') as docker:
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    module.recover(archive, manifest, output)
                docker.assert_not_called()
            self.assertFalse(output.exists())

    def test_remote_docker_refused_before_creation(self):
        with TemporaryDirectory() as directory:
            archive, manifest, output = self.inputs(directory)
            with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://unapproved.example:2375'}), \
                    patch.object(module.subprocess, 'check_output') as docker:
                with self.assertRaisesRegex(ValueError, 'local Unix'):
                    module.recover(archive, manifest, output)
                docker.assert_not_called()
            self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            archive, manifest, output = self.inputs(directory)
            output.mkdir()
            marker = output / 'preserved'
            marker.write_bytes(b'original')
            with patch.object(module.subprocess, 'check_output') as docker:
                with self.assertRaisesRegex(ValueError, 'new private'):
                    module.recover(archive, manifest, output)
                docker.assert_not_called()
            self.assertEqual(marker.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
