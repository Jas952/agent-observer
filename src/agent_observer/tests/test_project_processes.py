import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_observer import project_processes as processes
from agent_observer.local_services import LocalServiceManager


class ProjectProcessesTests(unittest.TestCase):
    def test_localized_device_dates_do_not_corrupt_process_commands(self):
        with patch.dict(os.environ,{'LC_ALL':'ru_RU.UTF-8'}):
            row=processes.scan(True)[os.getpid()]
        self.assertTrue(Path(row['command'].split()[0]).name.lower().startswith('python'),row['command'])

    def test_scope_redaction_and_stale_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); script=root/'worker.py';script.write_text('pass')
            base=dict(ppid=1,pgid=11,uid=os.getuid(),tty='ttys004',started_at='start',ports=[])
            rows={11:{**base,'pid':11,'cwd':str(root),'command':'node worker.js --token PRIVATE'},
                  12:{**base,'pid':12,'cwd':str(root)+'-other','command':'node unrelated.js'},
                  13:{**base,'pid':13,'cwd':'/tmp','command':f'{sys.executable} {script}'}}
            with patch.object(processes,'scan',return_value=rows):
                result=processes.inventory(root,set())
            self.assertEqual({row['pid'] for row in result},{11,13})
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(result[0]['origin'],'external_terminal')
            changed={**rows,11:{**rows[11],'started_at':'new process'}}
            with patch.object(processes,'scan',side_effect=[rows,changed]),patch.object(processes.os,'kill') as kill:
                with self.assertRaises(ValueError):processes.stop(root,set(),'process-11',processes.identity(rows[11]))
                kill.assert_not_called()

    def test_foreign_children_and_controllers_are_not_signalled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=dict(pgid=11,uid=os.getuid(),tty='??',started_at='start',ports=[],command='python worker.py')
            rows={11:{**base,'pid':11,'ppid':1,'cwd':str(root)},12:{**base,'pid':12,'ppid':11,'cwd':'/outside'}}
            with patch.object(processes,'scan',return_value=rows),patch.object(processes.os,'kill') as kill:
                with self.assertRaises(ValueError):processes.stop(root,set(),'process-11',processes.identity(rows[11]))
                with self.assertRaises(ValueError):processes.stop(root,{11},'process-11',processes.identity(rows[11]))
                kill.assert_not_called()

    def test_real_independent_service_survives_manager_shutdown_and_is_stoppable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            settings={'project_root':root,'port':port+1 if port<65535 else port-1,'history_file':root/'state/runs.json',
                      'local_services':[{'id':'proof','name':'Independent proof','cwd':'.','command':[sys.executable,'-u','-m','http.server',str(port),'--bind','127.0.0.1'],
                                         'endpoints':[f'http://127.0.0.1:{port}'],'health_kind':'http','health_url':f'http://127.0.0.1:{port}/','require_project_cwd':True}]}
            manager=LocalServiceManager(settings);other=None
            try:
                manager.start('proof',independent=True)
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    view=manager.snapshot()['services'][0]
                    if view['connected']:break
                    time.sleep(.05)
                self.assertTrue(view['connected']);self.assertEqual(view['owner'],'independent')
                manager.shutdown()
                with socket.create_connection(('127.0.0.1',port),timeout=1):pass
                other=LocalServiceManager(settings)
                current=other.snapshot()['services'][0]
                self.assertEqual(current['owner'],'independent')
                stopped=other.stop('proof',current['identity'])
                self.assertEqual(stopped['status'],'stopped')
            finally:
                for handle in manager.independent_handles.values():
                    if handle.poll() is None:handle.terminate()
                    handle.wait(timeout=4)
                if other:other.shutdown()
                manager.shutdown()

    def test_script_without_a_default_port_is_launchable_and_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'package.json').write_text(json.dumps({'scripts':{'site':'node services/server.js','monitor':'python3 -u monitor.py'}}))
            manager=LocalServiceManager({'project_root':root,'port':8766})
            try:
                rows=manager.snapshot()['services'];site=next(row for row in rows if row['id']=='project-site')
                self.assertTrue(site['can_start']);self.assertTrue(site['can_start_independent'])
                self.assertEqual(site['endpoints'],[])
                self.assertIn('npm',site['launch_command']);self.assertIn('site',site['launch_command'])
            finally:manager.shutdown()


if __name__=='__main__':unittest.main()
