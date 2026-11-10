from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
import sys,json,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.codex-work/jd-batch/python-deps'))
sys.path.insert(0,str(ROOT/'src'))
import jd_batch as core
import jd_batch_ai as ai
WORK=ROOT/'.codex-work/jd-batch/ai-tests';WORK.mkdir(parents=True,exist_ok=True)
core.DATA=WORK/'projects';core.OUTPUTS=WORK/'outputs';ai.CONFIG=WORK/'settings.json'

def field(value,source,quote=None,raw=None,unit=''):
    return {'value':value,'source':source['id'],'quote':quote or source['text'],'raw':raw if raw is not None else value,'unit':unit}

class AITests(unittest.TestCase):
    def setUp(self):ai.CONFIG.unlink(missing_ok=True)
    def config(self):return ai.save_config({'provider':'custom','baseUrl':'https://api.example.com/v1/chat/completions','model':'example-model','apiKey':'synthetic-secret-key'})
    def project(self):
        p=core.new_project('AI自动测试_非业务','government');core.add_file(p,'公告.txt','本次拍卖测试机器，起拍价为1.2万元。'.encode());p['files'][0]['text']='本次拍卖测试机器，起拍价为1.2万元。';p['analyzed']=True;core.save(p);return p
    def response(self,payload,price='12000'):
        s=payload['sources'][0];existing=payload['existingLots'];match=existing[0]['id'] if existing else ''
        return {'records':[{'matchId':match,'fields':{'拍品名称':field('测试机器',s),'起拍价':field(price,s,raw='1.2',unit='万元')}}],'shared':{},'issues':[]}

    def test_config_secret_and_endpoint_rules(self):
        result=self.config();self.assertNotIn('apiKey',result);self.assertNotIn('synthetic-secret-key',json.dumps(result));self.assertEqual(result['baseUrl'],'https://api.example.com/v1')
        ai.save_config({'provider':'custom','baseUrl':result['baseUrl'],'model':'second','apiKey':''});self.assertEqual(ai.read_config()['apiKey'],'synthetic-secret-key')
        with self.assertRaises(ValueError):ai.save_config({'provider':'custom','baseUrl':'https://else.example/v1','model':'m','apiKey':''})
        for url in ['http://api.example.com','https://127.0.0.1','https://198.18.1.65','https://localhost','https://x:y@api.example.com','https://api.example.com/?key=x']:
            with self.assertRaises(ValueError):ai.normalize_endpoint(url)

    def test_api_contract_and_truncated_response(self):
        self.config();calls=[]
        class Opener:
            def open(self,request,timeout):
                calls.append(request);return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'```json\n{"ok":true}\n```'}}]}).encode())
        addresses=[(2,1,6,'',('8.8.8.8',443))]
        with patch.object(ai.socket,'getaddrinfo',return_value=addresses),patch.object(ai,'build_opener',return_value=Opener()):self.assertEqual(ai.test_connection()['ok'],True)
        self.assertEqual(calls[0].full_url,'https://api.example.com/v1/chat/completions');self.assertEqual(json.loads(calls[0].data)['model'],'example-model');self.assertNotIn('response_format',json.loads(calls[0].data))
        class Truncated:
            def open(self,*args,**kwargs):return BytesIO(b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}')
        with patch.object(ai.socket,'getaddrinfo',return_value=addresses),patch.object(ai,'build_opener',return_value=Truncated()),self.assertRaisesRegex(ValueError,'未完整'):ai.complete('test',{})

    def test_private_dns_and_provider_error_redaction(self):
        self.config()
        for address in ['127.0.0.1','192.168.1.5','10.0.0.5','169.254.169.254','::1','fd00::1']:
            with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',(address,443))]),patch.object(ai,'build_opener') as opener,self.assertRaisesRegex(ValueError,'非公共网络'):
                ai.complete('test',{})
            opener.assert_not_called()
        class Failed:
            def open(self,*args,**kwargs):raise HTTPError('https://api.example.com',401,'synthetic-secret-key',{},BytesIO(b'secret'))
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(ai,'build_opener',return_value=Failed()):
            with self.assertRaises(ValueError) as raised:ai.complete('test',{})
            self.assertNotIn('synthetic-secret-key',str(raised.exception));self.assertIn('401',str(raised.exception))

    def test_tun_fake_ip_keeps_https_hostname_and_redirect_guard(self):
        self.config();calls=[]
        class Opener:
            def open(self,request,timeout):
                calls.append(request);return BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"ok\\":true}"}}]}')
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('198.18.1.65',443))]),patch.object(ai,'build_opener',return_value=Opener()) as build:
            self.assertTrue(ai.test_connection()['ok'])
        self.assertEqual(calls[0].full_url,'https://api.example.com/v1/chat/completions')
        self.assertTrue(any(isinstance(handler,ai.NoRedirect) for handler in build.call_args.args))
        with self.assertRaisesRegex(ValueError,'跳转'):ai.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_two_pass_corrects_then_applies_with_evidence(self):
        p=self.project();calls=[]
        def model(prompt,payload):calls.append(prompt);return self.response(payload,'12000' if prompt==ai.REVIEW_PROMPT else '1200')
        ai.recognize(p,complete_fn=model);self.assertEqual(len(calls),2);self.assertEqual(p['lots'][0]['fields']['起拍价'],'12000');self.assertEqual(p['ai']['status'],'completed');self.assertIn('quote',p['lots'][0]['aiEvidence']['起拍价'])

    def test_unfounded_fact_not_applied(self):
        p=self.project()
        def model(prompt,payload):
            r=self.response(payload);r['records'][0]['fields']['保证金金额']=field('5000',payload['sources'][0],raw='5000');return r
        ai.recognize(p,complete_fn=model);self.assertNotIn('保证金金额',p['lots'][0]['fields']);self.assertGreater(p['ai']['stats']['rejected'],0)
        ai.recognize(p,complete_fn=lambda prompt,payload:self.response(payload))
        self.assertEqual(p['aiIssues'],[])

    def test_manual_edit_preserved_and_stale_evidence_removed(self):
        p=self.project();ai.recognize(p,complete_fn=lambda prompt,data:self.response(data));edited=json.loads(json.dumps(p));edited['lots'][0]['fields']['起拍价']='13000';core.update_review(p,edited)
        self.assertNotIn('起拍价',p['lots'][0].get('aiEvidence',{}));ai.recognize(p,complete_fn=lambda prompt,data:self.response(data));self.assertEqual(p['lots'][0]['fields']['起拍价'],'13000')

    def test_failure_retains_parsed_data_and_does_not_apply_first_pass(self):
        p=self.project()
        def model(prompt,payload):
            if prompt==ai.REVIEW_PROMPT:raise ValueError('模拟服务失败')
            return self.response(payload)
        with self.assertRaises(ValueError):ai.recognize(p,complete_fn=model)
        saved=core.load(p['id']);self.assertEqual(saved['ai']['status'],'failed');self.assertEqual(saved['lots'],[]);self.assertTrue(saved['files'][0]['text'])

    def test_chunking_preserves_long_document_tail(self):
        p=self.project();p['files'][0]['text']='段落开始\n'+('测试长资料\n'*8000)+'最后一个标的价格987654'
        pieces=list(ai.chunks(p));self.assertGreater(len(pieces),1);self.assertIn('987654',pieces[-1]['text']);self.assertTrue(all(len(s['text'])<17000 for s in pieces))

    def test_context_limit_is_classified_without_exposing_provider_body(self):
        self.config()
        class Failed:
            def open(self,*args,**kwargs):raise HTTPError('https://api.example.com',400,'bad',{},BytesIO(b'{"error":"maximum context length exceeded synthetic-secret-key"}'))
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(ai,'build_opener',return_value=Failed()):
            with self.assertRaises(ai.AIError) as result:ai.complete('test',{})
        self.assertTrue(result.exception.split);self.assertNotIn('synthetic',str(result.exception));self.assertIn('上下文',str(result.exception))

    def test_compatible_model_retries_unsupported_json_and_system_role(self):
        self.config();config=ai.read_config();config['jsonMode']=True;calls=[]
        class Compatible:
            def open(self,request,timeout):
                body=json.loads(request.data);calls.append(body)
                if 'response_format' in body:raise HTTPError('',400,'',{},BytesIO(b'response_format unsupported'))
                if body['messages'][0]['role']=='system':raise HTTPError('',400,'',{},BytesIO(b'system role not supported'))
                return BytesIO(json.dumps({'choices':[{'message':{'content':[{'type':'text','text':'Result: {"ok":true}'}]}}]}).encode())
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(ai,'build_opener',return_value=Compatible()):self.assertTrue(ai.complete('test',{},config)['ok'])
        self.assertEqual(len(calls),3)

    def test_later_file_runs_after_failure_and_retry_skips_verified_file(self):
        p=self.project();core.add_file(p,'第二份.txt','本次拍卖测试机器，起拍价为1.2万元。'.encode());p['files'][1]['text']=p['files'][0]['text'];seen=[]
        def failed(prompt,payload):
            source=payload['sources'][0];seen.append(source['name'])
            if source['name']=='公告.txt':raise ai.AIError('模拟连接中断','AI_NETWORK')
            return self.response(payload)
        with self.assertRaisesRegex(ValueError,'未完成'):ai.recognize(p,complete_fn=failed)
        self.assertIn('第二份.txt',seen);self.assertEqual(len(p['lots']),1);self.assertEqual(p['ai']['status'],'partial')
        seen=[]
        def retry(prompt,payload):seen.append(payload['sources'][0]['name']);return self.response(payload)
        ai.recognize(p,complete_fn=retry)
        self.assertNotIn('第二份.txt',seen);self.assertEqual(p['ai']['status'],'completed')

    def test_empty_model_response_is_visible_and_not_marked_complete(self):
        p=self.project();calls=[]
        def empty(prompt,payload):calls.append(payload);return {'records':[],'shared':{},'issues':[]}
        with self.assertRaisesRegex(ValueError,'未完成'):ai.recognize(p,complete_fn=empty)
        self.assertEqual(len(calls),3);self.assertEqual(p['ai']['files'][0]['status'],'无可提取字段');self.assertNotEqual(p['ai']['status'],'completed')

    def test_capacity_retry_splits_one_source_and_preserves_tail(self):
        p=self.project();p['files'][0]['text']=('测试机器，起拍价1.2万元。\n'*80);seen=[]
        def small(prompt,payload):
            self.assertEqual(len(payload['sources']),1);text=payload['sources'][0]['text'];seen.append(text)
            if len(text)>900:raise ai.AIError('上下文不足','AI_CONTEXT',True)
            return self.response(payload)
        ai.recognize(p,complete_fn=small)
        self.assertGreater(len(seen),2);self.assertTrue(any('测试机器' in s for s in seen));self.assertEqual(p['ai']['status'],'completed')

    def test_sparse_schema_preserves_column_letters_without_blank_padding(self):
        preview=ai.schema_preview([['编号','','拍品名称']+['']*200+['起拍价']],'表',0,1,[])
        self.assertEqual(preview['rows'][0]['values']['C'],'拍品名称');self.assertEqual(len(preview['rows'][0]['values']),3)
        self.assertLess(len(json.dumps(preview,ensure_ascii=False)),300)

    def test_request_omits_unrelated_lots_and_keeps_template_small(self):
        p=self.project();p['lots']=[core.empty_lot('government','无关标的'+str(n)) for n in range(1000)]
        payload=ai.extraction_payload(p,next(ai.chunks(p)))
        self.assertEqual(payload['existingLots'],[]);self.assertLess(len(json.dumps(payload,ensure_ascii=False)),6500)

    def test_scanned_pdf_page_labels_are_not_treated_as_readable_text(self):
        p=self.project();p['files'][0].update(name='扫描.pdf',text='[第 1 页]\n[第 2 页]\n')
        self.assertFalse(core.has_document_text(p['files'][0]));self.assertEqual(list(ai.chunks(p)),[])
        with self.assertRaisesRegex(ValueError,'未读取到'):ai.recognize(p,complete_fn=lambda *_:self.fail('没有正文时不应请求 AI'))
        self.assertEqual(p['ai']['files'][0]['status'],'未读取')

    def test_streaming_reader_never_requests_entire_file(self):
        class Stream:
            remaining=51*1024*1024
            def read(self,size):
                self_test.assertLessEqual(size,1024*1024)
                if not self.remaining:return b''
                count=min(size,self.remaining);self.remaining-=count;return b'0'*count
        self_test=self;p=core.new_project('大文件单元测试','government');core.add_file_stream(p,'大文件.bin',Stream());self.assertEqual(p['files'][0]['size'],51*1024*1024)
        (core.project_path(p['id'])/'files'/p['files'][0]['id']).unlink()

if __name__=='__main__':unittest.main(verbosity=2)
