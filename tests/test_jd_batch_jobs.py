from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import importlib.util,threading,time,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jd_batch_server_test',ROOT/'scripts/jd-batch-server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class JobTests(unittest.TestCase):
    def test_public_page_navigation_does_not_allow_cross_site_data_access(self):
        headers={'Host':'127.0.0.1:4186','Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        request=SimpleNamespace(headers=headers,server=SimpleNamespace(server_port=4186))
        self.assertTrue(server.Handler.same_origin(request,True))
        self.assertFalse(server.Handler.same_origin(request))
        for change in ({'Sec-Fetch-Dest':'iframe'},{'Sec-Fetch-Mode':'cors'},{'Host':'example.com:4186'},{'Origin':'https://example.com'}):
            with self.subTest(change=change):
                request.headers=headers|change
                self.assertFalse(server.Handler.same_origin(request,True))

    def test_export_reports_measured_eta_and_rejects_overlapping_mutation(self):
        ready=threading.Event();release=threading.Event()
        def export(p,draft,progress):
            progress({'message':'正在处理图片','completed':0,'total':4})
            time.sleep(1.05)
            progress({'message':'已处理 1 张图片','completed':1,'total':4});ready.set()
            release.wait(5);return {'filename':'合成验证.zip'}
        with patch.object(server.core,'load',return_value={'id':'f'*32}),patch.object(server.core,'export',side_effect=export):
            job=server.start_job('f'*32,{'draft':True},'export')
            try:
                self.assertTrue(ready.wait(5))
                current=server.jobs[job['id']]
                self.assertEqual(current['state'],'running');self.assertGreater(current['percent'],0);self.assertLess(current['percent'],100)
                self.assertGreaterEqual(current['remainingSeconds'],2)
                with self.assertRaisesRegex(ValueError,'还有任务'):server.start_job('f'*32,{},'audit-run')
            finally:release.set()
            deadline=time.monotonic()+5
            while server.jobs[job['id']]['state']=='running' and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(current['state'],'completed');self.assertEqual(current['percent'],100)

if __name__=='__main__':unittest.main()
