"""No AWS/OpenAI calls, account access, credentials or MCP dependency required."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import threading
import unittest
from unittest.mock import patch

from jobwatch.postgres import connection_probe as probe


class ConnectivityTests(unittest.TestCase):
    def test_child_only_inherits_explicit_read_and_local_runtime_settings(self):
        env={'PATH':'/bin','HOME':'/synthetic','JOBWATCH_CHAT_DATABASE_URL':'read-only-synthetic',
             'PGPASSFILE':'/private/read-passfile','HF_HOME':'/synthetic/models',
             'OPENAI_API_KEY':'DO_NOT_INHERIT','CONTROL_PLANE_API_KEY':'DO_NOT_INHERIT',
             'AWS_PROFILE':'DO_NOT_INHERIT','AWS_SESSION_TOKEN':'DO_NOT_INHERIT',
             'JOBWATCH_DATABASE_URL':'DO_NOT_INHERIT','JOBWATCH_MIGRATION_DATABASE_URL':'DO_NOT_INHERIT',
             'JOBWATCH_EMBEDDING_DATABASE_URL':'DO_NOT_INHERIT','PGHOSTADDR':'DO_NOT_INHERIT',
             'PGOPTIONS':'DO_NOT_INHERIT'}
        result=probe.server_environment(env)
        self.assertEqual(result,{key:env[key] for key in ('PATH','HOME','JOBWATCH_CHAT_DATABASE_URL','PGPASSFILE','HF_HOME')})

    def test_probe_accepts_only_explicit_loopback_mcp_http(self):
        valid='http://127.0.0.1:8765/mcp'
        self.assertEqual(probe.loopback_url(valid),valid)
        for bad in ('https://example.org/mcp','http://localhost:8765/mcp','http://127.0.0.1/mcp',
                    valid+'?token=secret',valid+'#secret','http://user:secret@127.0.0.1:8765/mcp',
                    'http://127.0.0.1:99999/mcp','http://127.0.0.1:8765/admin'):
            with self.subTest(value=bad),self.assertRaises(ValueError):probe.loopback_url(bad)

    def test_cli_failure_redacts_url_credentials_and_exception_details(self):
        output=io.StringIO()
        with redirect_stdout(output):code=probe.main(['--http','http://user:PRIVATE_SECRET@127.0.0.1:8765/mcp'])
        self.assertEqual(code,2);self.assertFalse(json.loads(output.getvalue())['passed'])
        self.assertNotIn('PRIVATE_SECRET',output.getvalue());self.assertNotIn('127.0.0.1',output.getvalue())
        async def broken(_):raise RuntimeError('PRIVATE_DRIVER_AND_OWNER_DATA')
        output=io.StringIO()
        with patch.object(probe,'run',broken),redirect_stdout(output):code=probe.main([])
        self.assertEqual(code,2);self.assertNotIn('PRIVATE_DRIVER',output.getvalue())

    @unittest.skipUnless(importlib.util.find_spec('mcp'),'Optional official MCP runtime')
    def test_loopback_probe_does_not_follow_http_redirects(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        visited=[]
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                visited.append(self.path)
                self.send_response(307)
                self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}/private')
                self.send_header('Content-Length','0');self.end_headers()
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with redirect_stdout(io.StringIO()):code=probe.main(['--http',f'http://127.0.0.1:{server.server_port}/mcp'])
            self.assertEqual(code,2);self.assertEqual(visited,['/mcp'])
        finally:server.shutdown();server.server_close();thread.join()

    @unittest.skipUnless(importlib.util.find_spec('mcp'),'Optional official MCP runtime')
    def test_mcp_disabled_default_never_invokes_local_model(self):
        from jobwatch.postgres.read_mcp import server
        from jobwatch.postgres.read_tools import ReadTools,ReadError
        core=ReadTools()
        with patch('jobwatch.postgres.embeddings.query_embedding',side_effect=AssertionError('Model must not load')) as inference:
            server(core)
            with self.assertRaises(ReadError) as error:core.search('Python',method='vector')
            self.assertEqual(error.exception.code,'query_inference_disabled')
            inference.assert_not_called()
        with self.assertRaises(ValueError):server(query_inference='remote_unapproved')
