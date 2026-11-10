from pathlib import Path
from io import BytesIO
from copy import deepcopy
from unittest.mock import patch
import json,sys,unittest,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.codex-work/jd-batch/python-deps')]
import openpyxl
import jd_batch as core
import jd_batch_audit as audit
import jd_batch_jd as jd
from test_jd_batch import picture

def book(rows):
    wb=openpyxl.Workbook()
    for row in rows:wb.active.append(row)
    out=BytesIO();wb.save(out);return out.getvalue()

class AuditTests(unittest.TestCase):
    def setUp(self):
        core.DATA=ROOT/'.codex-work/jd-batch/audit-tests/projects'
        self.p=core.new_project('合成复核项目','government')
        data=[['标的编号','拍品名称','起拍价','专场名称','鉴定结论'],['C001','C001测试设备甲',16.7,'场甲','符合'],['C002','C002测试设备乙',19,'场甲','符合'],['C003','C003不上拍设备',20,'场甲','不符合'],['D001','D001其他专场',30,'场乙','符合']]
        core.add_file(self.p,'原始安排.xlsx',book(data));core.analyze(self.p,False)
        core.apply_common(self.p,{'rates':{'保证金金额':'20'}})
        self.reference=audit.baseline(self.p)
        self.export={'records':[{'sessionId':'123','sessionName':'场甲','lotId':'9001','拍品名称':'拟处置C001测试设备甲（以实物为准）','起拍价':'16.7','保证金金额':'3.34'}, {'sessionId':'123','sessionName':'场甲','lotId':'9002','拍品名称':'C002测试设备乙','起拍价':'19','保证金金额':'3.8'}],'issues':[],'duplicateRows':0}
    def compare(self):return audit.compare(self.p,self.export,self.reference,'123','场甲')

    def test_correct_membership_price_rules_and_exclusions(self):
        result=self.compare();self.assertEqual(result['differences'],[]);self.assertEqual(result['expectedCount'],2);self.assertEqual(result['excludedCount'],1)
        check=next(c for c in result['rows'][0]['checks'] if c['field']=='起拍价')
        self.assertEqual(check['expected'],'16.7');self.assertEqual(check['basis'],'原始表格')

    def test_removed_editor_lot_cannot_erase_raw_baseline(self):
        self.p['lots']=[];reference=audit.baseline(self.p);self.assertEqual(len(reference['plans']),3)
        self.export['records'].pop();result=audit.compare(self.p,self.export,reference,'123','场甲')
        self.assertTrue(any('漏拍品' in x for x in result['differences']))

    def test_wrong_price_deposit_extra_and_cross_session(self):
        self.export['records'][0].update(起拍价='150',保证金金额='30')
        self.export['records'][1]['sessionId']='456'
        self.export['records'].append({'sessionId':'123','lotId':'9003','拍品名称':'C003不上拍设备','起拍价':'20','保证金金额':'4'})
        result=self.compare();text=' '.join(result['differences'])
        for term in ['起拍价','保证金金额','其他专场 456','多出']:self.assertIn(term,text)

    def test_manual_price_does_not_override_raw_reference(self):
        self.p['lots'][0]['fields']['起拍价']='500';self.p['lots'][0]['sources']['起拍价']='人工核对'
        self.assertEqual(self.compare()['differences'],[])

    def test_missing_deposit_not_passed(self):
        self.export['records'][0]['保证金金额']=''
        self.assertTrue(any('保证金金额' in x for x in self.compare()['unknown']))

    def test_identical_title_without_code_is_ambiguous(self):
        for plan in self.reference['plans'][:2]:plan['fields']['拍品名称']='相同设备';plan['grouping']['codes']=[]
        for row in self.export['records']:row['拍品名称']='相同设备'
        self.assertTrue(any('唯一对应' in x for x in self.compare()['unknown']))

    def test_export_parser_repeated_orders_and_conflict(self):
        rows=[['导出表说明'],['拍品名称','保证金支付金额（元）','专场编号','拍品编号','起拍价(元)','保证金(元)'],['C001测试',999,'123','456',15,3],['C001测试',888,'123','456',15,3]]
        path=core.project_path(self.p['id'])/'test.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('../../不会解压.xlsx',book(rows))
        parsed=audit.read_export(path);self.assertEqual(len(parsed['records']),1);self.assertEqual(parsed['duplicateRows'],1);self.assertEqual(parsed['issues'],[])
        self.assertEqual(parsed['records'][0]['保证金金额'],'3')
        rows[-1][-1]=30
        with zipfile.ZipFile(path,'w') as z:z.writestr('export.xlsx',book(rows))
        self.assertIn('冲突',audit.read_export(path)['issues'][0])

    def test_off_switch_makes_no_network_or_vision_request(self):
        identifier='a'*32;root=audit.folder(self.p,identifier);root.mkdir(parents=True,exist_ok=True);core.json_write(root/'parsed.json',self.export)
        with patch.object(jd,'review_images',side_effect=AssertionError('OFF must not call image review')):
            result=audit.run(self.p,{'auditId':identifier,'sessionId':'123','sourceSession':'场甲','images':False})
        self.assertFalse(result['imageEnabled']);self.assertTrue((root/'report.html').exists())

    def test_exact_pixel_match_skips_ai(self):
        blob=picture()
        result=jd.compare_photo(blob,[blob],{},call=lambda *a,**kw:self.fail('identical image must not call AI'))
        self.assertEqual(result,{'state':'像素一致','index':0})

    def test_vision_batches_all_candidates_and_invalid_ai_is_unknown(self):
        from PIL import Image
        def image(color):
            b=BytesIO();Image.new('RGB',(30,30),color).save(b,'PNG');return b.getvalue()
        calls=[]
        def model(*args,**kwargs):calls.append(kwargs);return {'verdict':'match','candidate':99}
        result=jd.compare_photo(image('red'),[image('blue')]*7,{},call=model)
        self.assertEqual(len(calls),2);self.assertLessEqual(len(calls[0]['images']),5);self.assertEqual(result['state'],'未核实')

    def test_failed_jd_request_marks_unknown(self):
        core.add_file(self.p,'C001/照片.png',picture());self.p['lots'][0]['imageIds']=[self.p['files'][-1]['id']]
        result=self.compare()
        with patch.object(jd,'public_images',side_effect=ValueError('需要登录')),patch.object(jd.ai,'read_config',return_value={}):jd.review_images(self.p,result)
        self.assertEqual(result['rows'][0]['imageState'],'未核实');self.assertTrue(any('图片未完全核实' in x for x in result['unknown']))

    def test_unsafe_remote_hosts_rejected_before_network(self):
        with patch.object(jd.socket,'getaddrinfo',side_effect=AssertionError('no network')):
            for url in ['https://127.0.0.1/x','https://img12.360buyimg.com.evil.test/x','http://img12.360buyimg.com/x']:
                with self.assertRaises(ValueError):jd.read_url(url)

if __name__=='__main__':unittest.main()
