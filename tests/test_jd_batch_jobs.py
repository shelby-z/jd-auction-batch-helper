from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from urllib.request import Request,build_opener,ProxyHandler
import json
import importlib.util,threading,time,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jd_batch_server_test',ROOT/'scripts/jd-batch-server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class JobTests(unittest.TestCase):
    def test_cancel_api_stops_wait_without_applying_late_response(self):
        server.core.DATA=ROOT/'.codex-work/jd-batch/cancel-tests/projects'
        p=server.core.new_project('合成停止测试','government');p['analyzed']=True
        p['files']=[{'id':'f','name':'说明.txt','kind':'document','text':'测试资料','status':'已读取'}];server.core.save(p)
        ready=threading.Event();release=threading.Event();recognize=server.ai.recognize
        def delayed(*args):ready.set();release.wait(5);return {'records':[]}
        def run(project,progress,cancel):return recognize(project,progress,complete_fn=delayed,cancel=cancel)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
        try:
            with patch.object(server.ai,'recognize',side_effect=run):
                job=server.start_job(p['id'],{'ai':True})
                self.assertTrue(ready.wait(3));self.assertTrue(job['cancellable'])
                url=f'http://127.0.0.1:{http.server_port}/api/jobs/{job["id"]}/cancel'
                with build_opener(ProxyHandler({})).open(Request(url,data=b'{}',headers={'Content-Type':'application/json'}),timeout=3) as response:self.assertEqual(response.status,200)
                deadline=time.monotonic()+2
                while server.jobs[job['id']]['state']=='running' and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(server.jobs[job['id']]['state'],'failed');self.assertIn('已停止',server.jobs[job['id']]['message'])
                self.assertEqual(server.core.load(p['id'])['lots'],[])
        finally:release.set();http.shutdown();http.server_close()

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
        def export(p,draft,progress,**kwargs):
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
