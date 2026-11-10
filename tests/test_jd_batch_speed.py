"""Structured-table fast path must preserve all rows, prices and exclusions."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import copy,json,sys,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.codex-work/jd-batch/python-deps')]
import openpyxl
import jd_batch as core,jd_batch_ai as ai,jd_batch_audit as audit


class SpeedTests(unittest.TestCase):
    def setUp(self):
        core.DATA=ROOT/'.codex-work/jd-batch/speed-tests/projects';ai.CONFIG=ROOT/'.codex-work/jd-batch/speed-tests/no-config.json'

    def fixture(self):
        wb=openpyxl.Workbook();ws=wb.active;ws.title='无表头'
        ws.append([16.7,'T001-T002组合设备','T001','符合','专场甲',3])
        ws.append([None,None,'T002','符合',None,None]);ws.merge_cells('A1:A2');ws.merge_cells('B1:B2');ws.merge_cells('E1:E3')
        ws.append([9,'T003不符合设备','T003','不符合',None,2])
        out=BytesIO();wb.save(out);return out.getvalue()

    def schema(self):
        fact=lambda col,value:{'column':col,'header':'','sampleRow':1,'sample':value}
        return {'tables':[{'sheet':'无表头','headerRow':0,'dataStartRow':1,'columns':{'id':fact('C','T001'),'name':fact('B','T001-T002组合设备'),'price':fact('A','16.7'),'session':fact('E','专场甲'),'status':fact('D','符合')},'fields':{'保证金金额':fact('F','3')}}],'issues':[]}

    def test_headerless_table_uses_two_schema_calls_and_local_checks(self):
        p=core.new_project('合成无表头性能验证','government');core.add_file(p,'清单.xlsx',self.fixture());core.analyze(p,False);calls=[]
        def model(prompt,payload):
            calls.append(payload);self.assertIn('sheets',payload);return self.schema()
        ai.recognize(p,complete_fn=model)
        self.assertEqual(len(calls),2);self.assertEqual(p['ai']['stats']['requests'],2)
        self.assertEqual(p['ai']['status'],'completed');self.assertEqual(len(p['lots']),1)
        lot=p['lots'][0];self.assertEqual(lot['fields']['起拍价'],'16.7');self.assertEqual(lot['fields']['保证金金额'],'3')
        self.assertEqual(lot['grouping']['rows'],[1,2]);self.assertEqual(lot['grouping']['sessionName'],'专场甲')
        self.assertEqual([x['code'] for x in p['classification']['excluded']],['T003'])
        reference=audit.baseline(p);self.assertEqual(reference['issues'],[])
        self.assertEqual(reference['plans'][0]['fields']['起拍价'],'16.7');self.assertEqual(reference['plans'][0]['fields']['保证金金额'],'3')
        self.assertEqual([x['code'] for x in reference['excluded']],['T003'])

    def test_headerless_sample_evidence_cannot_be_invented(self):
        tables=core.read_document(self.fixture(),'清单.xlsx',False)[1];schema=self.schema()
        schema['tables'][0]['columns']['price']['sample']='9999'
        with self.assertRaisesRegex(ValueError,'不一致'):ai.grouping_hints(schema,tables)

    def test_headerless_start_cannot_skip_existing_assets(self):
        tables=core.read_document(self.fixture(),'清单.xlsx',False)[1];schema=self.schema()
        schema['tables'][0]['dataStartRow']=3
        with self.assertRaisesRegex(ValueError,'跳过'):ai.grouping_hints(schema,tables)

    def test_mapped_sheet_is_not_resent_but_other_sheet_is_retained(self):
        p={'files':[{'id':'f','name':'工作簿.xlsx','kind':'document','text':'\n[工作表：已核验]\n第 1 行\t核验内容\n[工作表：合同说明]\n付款期限：3天'}]}
        chunks=list(ai.chunks(p,handled={'f':{'已核验'}}))
        self.assertEqual(len(chunks),1);self.assertNotIn('核验内容',chunks[0]['text']);self.assertIn('付款期限：3天',chunks[0]['text'])

    def test_ark_disables_thinking_but_other_providers_keep_their_contract(self):
        requests=[]
        class Opener:
            def open(self,request,timeout):
                requests.append(json.loads(request.data));return BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"ok\\":true}"}}]}')
        config={'model':'doubao-seed-2.1-pro','apiKey':'synthetic-only','baseUrl':'https://ark.cn-beijing.volces.com/api/plan/v3'}
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(ai,'build_opener',return_value=Opener()):
            ai.complete('测试',{},config);ai.complete('测试',{},config|{'baseUrl':'https://api.example.com/v1'})
        self.assertEqual(requests[0]['thinking'],{'type':'disabled'});self.assertNotIn('thinking',requests[1])

if __name__=='__main__':unittest.main()
