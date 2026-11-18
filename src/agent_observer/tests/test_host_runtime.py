import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_observer import host_runtime
from agent_observer.installation import DASHBOARD_PROTOCOL


class HostRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.patch=patch.object(host_runtime,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(host_runtime.release_start_lock)

    def row(self,pid,port,command=None):
        return {'pid':pid,'ppid':0,'uid':os.getuid(),'started_at':'fixture-start','cwd':str(self.root),
                'command':command or f'{sys.executable} {self.root}/observer.py run','ports':[{'host':'127.0.0.1','port':port}]}

    def test_unrelated_listener_is_not_terminated_or_replaced(self):
        rows={9001:self.row(9001,8766,'python3 -m http.server 8766')}
        self.assertFalse(host_runtime._verified(self.row(9002,8766,f'{sys.executable} another.py {self.root}/observer.py run'),host_runtime._scripts()))
        with patch('agent_observer.project_processes.scan',return_value=rows),patch.object(host_runtime.os,'kill') as kill:
            with self.assertRaisesRegex(ValueError,'unrelated'):host_runtime.prepare()
        kill.assert_not_called()

    def test_stale_verified_hosts_are_replaced_but_unrelated_background_work_survives(self):
        rows={9001:self.row(9001,8766),9002:self.row(9002,18768),9003:self.row(9003,3333,'node worker.mjs')}
        remaining={9003:rows[9003]};sock=MagicMock()
        with patch('agent_observer.project_processes.scan',side_effect=[rows,rows,remaining]),patch.object(host_runtime.os,'kill') as kill,patch.object(host_runtime.socket,'socket',return_value=sock),patch.object(host_runtime.urllib.request,'build_opener',side_effect=OSError('stale host')),patch.object(host_runtime,'_migrate_ports'):
            self.assertIs(host_runtime.prepare(),sock)
        self.assertEqual({call.args[0] for call in kill.call_args_list},{9001,9002})
        sock.bind.assert_called_once_with(('127.0.0.1',8766))

    def test_compatible_host_is_reused_instead_of_starting_another_listener(self):
        rows={9001:self.row(9001,8766)}
        response=io.BytesIO(json.dumps({'service':'agent-observer','dashboard_protocol':DASHBOARD_PROTOCOL,'server_revision':'current'}).encode())
        opener=MagicMock();opener.open.return_value=response
        with patch('agent_observer.project_processes.scan',return_value=rows),patch.object(host_runtime,'revision',return_value='current'),patch.object(host_runtime.urllib.request,'build_opener',return_value=opener),patch.object(host_runtime.os,'kill') as kill,patch.object(host_runtime.socket,'socket') as socket,patch.object(host_runtime,'_migrate_ports'):
            self.assertIsNone(host_runtime.prepare())
        kill.assert_not_called();socket.assert_not_called()


if __name__=='__main__':unittest.main()
