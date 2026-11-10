from pathlib import Path
from io import BytesIO
from unittest.mock import patch
import copy,json,sys,threading,time,unittest,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import jd_batch as core,jd_batch_ai as ai,jd_batch_classification as groups
import openpyxl
from PIL import Image

class ManualTests(unittest.TestCase):
    def setUp(self):
        core.DATA=ROOT/'.codex-work/jd-batch/manual-tests/projects';core.OUTPUTS=ROOT/'.codex-work/jd-batch/manual-tests/outputs';ai.CONFIG=core.DATA/'no-config.json'

    def project(self):
        p=core.new_project('合成子编号验证','government');wb=openpyxl.Workbook();ws=wb.active
        ws.append(['标的物编号','拍品名称','起拍价'])
        for i in range(1,6):ws.append([f'B243-{i}','B243-1--B243-5测试组合' if i==1 else None,6750 if i==1 else None])
        ws.merge_cells('B2:B6');ws.merge_cells('C2:C6');buf=BytesIO();wb.save(buf);core.add_file(p,'清单.xlsx',buf.getvalue())
        for i in range(1,6):
            buf=BytesIO();Image.new('RGB',(20,20)).save(buf,'PNG');core.add_file(p,f'B243/243-{i}/照片.png',buf.getvalue())
        core.analyze(p,False);return p

    def test_child_codes_ranges_and_inherited_folder_prefix(self):
        known={f'B243-{i}' for i in range(1,6)}|{'B243','B244','B243-10'}
        for title in ['B243-1--B243-5测试','B243-1—B243-5测试','B243-1到B243-5测试']:
            self.assertEqual(groups.title_codes(title,known),{f'B243-{i}' for i in range(1,6)})
        self.assertEqual(groups.image_codes('B243/243-2/照片.jpg',known),{'B243-2'})
        self.assertEqual(groups.image_codes('B243/B243-10/照片.jpg',known),{'B243-10'})
        self.assertEqual(groups.image_codes('B243/243-9/照片.jpg',known),set())
        self.assertEqual(groups.title_codes('B301到B343一批',{'B301','B320','B343','B344'}),{'B301','B320','B343'})
        p=self.project();self.assertEqual(len(p['lots']),1);self.assertEqual(len(p['lots'][0]['imageIds']),5);self.assertEqual(p['lots'][0]['fields']['起拍价'],'6750')

    def test_parent_folder_can_cover_all_children_and_manual_order_is_preserved(self):
        self.assertEqual(groups.image_codes('B243/整批.jpg',{'B243-1','B243-2'}),{'B243-1','B243-2'})
        p=self.project();lot=p['lots'][0];lot['imageIds']=lot['imageIds'][1:][::-1];lot['manualImages']=True;lot.pop('grouping');lot['fields']['拍品名称']='B243-1—B243-5测试组合';lot['sources']['拍品名称']='人工核对'
        before=copy.deepcopy(lot);f=p['files'][0];metadata={};_,tables,_=core.read_document(core.project_path(p['id'])/'files'/f['id'],f['name'],False,metadata)
        plan=groups.parse_tables(tables,metadata,f['name'],core.normalize_value)
        core.apply_classification(p,plan['plans'],{'files':[f['id']],'excluded':[],'pending':[]})
        self.assertEqual(p['lots'][0]['id'],before['id']);self.assertEqual(p['lots'][0]['imageIds'],before['imageIds']);self.assertEqual(p['lots'][0]['fields']['拍品名称'],before['fields']['拍品名称'])

    def test_batch_photos_are_a_warning_and_manual_export_keeps_all_reminders(self):
        p=self.project();lot=p['lots'][0];lot['imageIds']=lot['imageIds'][:1]
        result=core.validate(p);self.assertTrue(any('整批共用照片' in x for x in result['warnings']))
        self.assertFalse(any('分类组合缺少图片' in x['message'] for x in result['errors']))
        lot['imageIds']=[]
        with self.assertRaisesRegex(ValueError,'勾选'):core.export(p,allow_issues=True)
        p['reviewed']=True;result=core.export(p,allow_issues=True)
        with zipfile.ZipFile(core.OUTPUTS/p['id']/result['filename']) as z:self.assertIn('图片/1/',z.namelist())
        report=json.loads((core.OUTPUTS/p['id']/result['report']).read_text(encoding='utf8'))
        self.assertTrue(report['manualOverride']);self.assertTrue(any(e['field']=='图片' for e in report['validation']['errors']))
        lot['fields']['图片文件夹名称']='../非法'
        with self.assertRaisesRegex(ValueError,'安全'):core.export(p,allow_issues=True)

    def test_schema_failure_does_not_start_full_table_fallback(self):
        p=self.project();before=copy.deepcopy(p['lots']);calls=[]
        def model(prompt,payload):calls.append(payload);return {'tables':[{'sheet':'不存在','headerRow':1,'columns':{}}]}
        with self.assertRaisesRegex(ValueError,'分类表'):ai.recognize(p,complete_fn=model)
        self.assertEqual(len(calls),2);self.assertEqual(p['lots'],before)

    def test_cancel_and_timeout_discard_late_model_response(self):
        ready=threading.Event();release=threading.Event();cancel=threading.Event()
        def delayed(*args):ready.set();release.wait(3);return {'late':True}
        threading.Timer(.05,cancel.set).start()
        try:
            with self.assertRaisesRegex(ValueError,'已停止'):ai.cancellable_request(delayed,'',{},cancel)
            with self.assertRaisesRegex(ValueError,'超过'):ai.cancellable_request(delayed,'',{},timeout=.03)
        finally:release.set()

if __name__=='__main__':unittest.main()
