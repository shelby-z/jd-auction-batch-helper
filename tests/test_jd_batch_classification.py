from pathlib import Path
from io import BytesIO
import sys,json,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.codex-work/jd-batch/python-deps'));sys.path.insert(0,str(ROOT/'src'))
import jd_batch as core
import jd_batch_ai as ai
import openpyxl,xlwt
from PIL import Image
WORK=ROOT/'.codex-work/jd-batch/classification-tests';WORK.mkdir(parents=True,exist_ok=True)

def image():
    out=BytesIO();Image.new('RGB',(60,40),'blue').save(out,'PNG');return out.getvalue()

def fixture(renamed=False):
    wb=xlwt.Workbook();ws=wb.add_sheet('上拍安排')
    headers=['市场行情价','编码' if renamed else '标的物编号','本组起拍金额' if renamed else '金额','挂牌标题' if renamed else '拍品名称','物品名称']
    for c,v in enumerate(headers):ws.write(0,c,v)
    for r,values in enumerate([[500,'B001',None,None,'同种设备'],[500,'B002',None,None,'同种设备'],[800,'B003',3000,'B003单独设备','同种设备']],1):
        for c,v in enumerate(values):
            if v is not None:ws.write(r,c,v)
    ws.write_merge(1,2,2,2,2000);ws.write_merge(1,2,3,3,'B001-B002合拍设备')
    out=BytesIO();wb.save(out);return out.getvalue()

class ClassificationTests(unittest.TestCase):
    def setUp(self):
        core.DATA=WORK/'projects';core.OUTPUTS=WORK/'outputs';ai.CONFIG=WORK/'not-configured.json'
    def project(self,renamed=False):
        p=core.new_project('合成分类表测试','government');core.add_file(p,'安排.xls',fixture(renamed))
        for name in ['B001/1.png','B002/1.png','B003/1.png']:core.add_file(p,name,image())
        core.analyze(p,False);return p
    def schema(self):
        return {'tables':[{'sheet':'上拍安排','headerRow':1,'columns':{'id':{'column':'B','header':'编码'},'name':{'column':'D','header':'挂牌标题'},'price':{'column':'C','header':'本组起拍金额'}}}],'issues':[]}

    def test_merged_listing_groups_use_total_price_not_inventory_price(self):
        p=self.project();self.assertEqual(len(p['lots']),2)
        self.assertEqual([l['fields']['起拍价'] for l in p['lots']],['2000','3000'])
        self.assertEqual(p['lots'][0]['grouping']['codes'],['B001','B002'])
        self.assertEqual([len(l['imageIds']) for l in p['lots']],[2,1])
        self.assertEqual([l['fields']['图片文件夹名称'] for l in p['lots']],['1','2'])

    def test_ai_identifies_renamed_reordered_columns_from_verified_headers(self):
        p=self.project(True);calls=[]
        def model(prompt,payload):calls.append(payload);return self.schema()
        ai.recognize_grouping(p,model)
        self.assertEqual(len(calls),2);self.assertTrue(p['classification']['aiRecognized'])
        self.assertEqual([l['fields']['起拍价'] for l in p['lots']],['2000','3000'])
        self.assertEqual(p['lots'][0]['grouping']['titleColumn'],4)
        self.assertEqual([len(l['imageIds']) for l in p['lots']],[2,1])
        tables=core.read_document(fixture(True),'安排.xls',False)[1];bad=self.schema();bad['tables'][0]['columns']['price']['header']='编造表头'
        with self.assertRaisesRegex(ValueError,'不一致'):ai.grouping_hints(bad,tables)

    def test_xlsx_merges_and_metadata_survive_manual_save(self):
        wb=openpyxl.Workbook();ws=wb.active;ws.append(['拍品名称','金额','标的物编号'])
        ws.append(['C001-C002组合',800,'C001']);ws.append([None,None,'C002']);ws.merge_cells('A2:A3');ws.merge_cells('B2:B3')
        out=BytesIO();wb.save(out);p=core.new_project('XLSX分类','government');core.add_file(p,'分类.xlsx',out.getvalue());core.analyze(p,False)
        self.assertEqual(p['lots'][0]['grouping']['codes'],['C001','C002']);self.assertEqual(len(p['lots']),1)
        payload=json.loads(json.dumps(p));payload['lots'][0]['fields']['联系人']='测试联系人';core.update_review(p,payload)
        self.assertEqual(p['lots'][0]['grouping']['codes'],['C001','C002'])

    def test_existing_template_folder_is_preserved_and_matches_photos(self):
        wb=openpyxl.Workbook();ws=wb.active;ws.append(['拍品名称','起拍价','图片文件夹名称'])
        ws.append(['单独设备',800,'照片_07']);out=BytesIO();wb.save(out)
        p=core.new_project('已填模板','government');core.add_file(p,'模板.xlsx',out.getvalue());core.add_file(p,'图片/照片_07/原照片.png',image());core.analyze(p,False)
        self.assertEqual(p['lots'][0]['fields']['图片文件夹名称'],'照片_07');self.assertEqual(len(p['lots'][0]['imageIds']),1)

    def test_partial_ai_schema_does_not_remove_another_classification(self):
        p=self.project(True)
        p['classification']={'files':['other-table'],'excluded':[],'pending':[]}
        before=json.loads(json.dumps(p['lots']))
        ai.recognize_grouping(p,lambda prompt,payload:self.schema())
        self.assertEqual(p['lots'],before)
        self.assertTrue(any('未确认全部' in issue for issue in p['aiIssues']))

    def test_wrong_or_missing_group_images_are_reported(self):
        p=self.project();a,b=p['lots'];a['imageIds'].append(b['imageIds'].pop());errors=core.validate(p)['errors']
        self.assertTrue(any('不属于该分类组合' in e['message'] for e in errors))
        self.assertTrue(any('缺少图片：B003' in e['message'] for e in errors))

    def legacy_project(self):
        p=self.project();p.pop('classification')
        for lot in p['lots']:
            lot.pop('grouping');lot['fields'].pop('起拍价');lot['sources'].pop('起拍价');lot['imageIds']=[]
        extra=core.empty_lot(p['template'],'旧版误拆的单件');p['lots'].append(extra)
        core.apply_common(p,{'fields':{'拍卖具体类目':'酒水','联系人':'统一联系人'},'rates':{'保证金金额':'20'},'depositMinimum':'300'})
        # Earlier manual fills equal to a saved common value must not block grouping.
        extra=p['lots'][-1]
        extra['sources']['拍卖具体类目']='人工核对';extra['commonOverrides']=['拍卖具体类目']
        return p

    def standard_schema(self):
        result=self.schema();cols=result['tables'][0]['columns']
        cols['id']['header']='标的物编号';cols['name']['header']='拍品名称';cols['price']['header']='金额'
        return result

    def test_legacy_manual_common_fields_do_not_block_prices_and_images(self):
        p=self.legacy_project();old_id=p['lots'][0]['id']
        p['lots'][0]['fields']['联系人']='单独联系人';p['lots'][0]['sources']['联系人']='人工核对'
        ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        self.assertEqual(len(p['lots']),2);self.assertEqual(p['lots'][0]['id'],old_id)
        self.assertEqual([l['fields']['起拍价'] for l in p['lots']],['2000','3000'])
        self.assertEqual([l['fields']['保证金金额'] for l in p['lots']],['400','600'])
        self.assertEqual([len(l['imageIds']) for l in p['lots']],[2,1])
        self.assertEqual(p['lots'][0]['fields']['联系人'],'单独联系人')
        self.assertTrue(list((core.project_path(p['id'])/'history').glob('before-classification-*.json')))

    def test_regroup_preserves_manual_price_folder_and_main_photo(self):
        p=self.project();first=p['lots'][0];first['imageIds'].reverse();first['manualImages']=True
        for k,v in {'起拍价':'2100','图片文件夹名称':'单独目录'}.items():first['fields'][k]=v;first['sources'][k]='人工核对'
        image_ids=first['imageIds'][:];old_id=first['id']
        ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        self.assertEqual(p['lots'][0]['fields']['起拍价'],'2100');self.assertEqual(p['lots'][0]['fields']['图片文件夹名称'],'单独目录')
        self.assertEqual(p['lots'][0]['imageIds'],image_ids);self.assertEqual(p['lots'][0]['id'],old_id)

    def test_unmatched_individual_edit_blocks_regroup_without_partial_changes(self):
        p=self.legacy_project();extra=p['lots'][-1];extra['fields']['联系人']='特殊联系人';extra['sources']['联系人']='人工核对'
        before=json.loads(json.dumps(p))
        with self.assertRaisesRegex(ValueError,'无法唯一对应'):ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        self.assertEqual(p,before)

    def test_old_browser_cannot_restore_legacy_rows_after_regroup(self):
        p=self.legacy_project();stale=json.loads(json.dumps(p))
        ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        with self.assertRaisesRegex(ValueError,'刷新页面'):core.update_review(p,stale)
        self.assertEqual(len(p['lots']),2)
        refreshed=json.loads(json.dumps(p));core.update_review(p,refreshed)
        self.assertEqual([len(l['imageIds']) for l in core.load(p['id'])['lots']],[2,1])

    def test_repeated_grouping_is_stable(self):
        p=self.legacy_project();ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        before=json.loads(json.dumps(p));ai.recognize_grouping(p,lambda prompt,payload:self.standard_schema())
        self.assertEqual(p,before)

    def test_negative_appraisal_is_excluded_even_with_listing_title_and_price(self):
        wb=openpyxl.Workbook();ws=wb.active;ws.append(['标的物编号','拍品名称','起拍价','鉴定结论'])
        ws.append(['D001','D001酒水',500,'不符合']);out=BytesIO();wb.save(out)
        p=core.new_project('鉴定不符合测试','government');core.add_file(p,'鉴定.xlsx',out.getvalue());core.add_file(p,'D001/照片.png',image())
        core.analyze(p,False)
        self.assertEqual(p['lots'],[]);self.assertEqual([x['code'] for x in p['classification']['excluded']],['D001'])
        self.assertEqual(p['classification']['excluded'][0]['reason'],'鉴定不符合');self.assertTrue(p['files'][1]['ignored'])

    def test_excluded_items_cannot_be_reintroduced_by_manual_photo_assignment(self):
        p=self.project();core.add_file(p,'B004/照片.png',image())
        p['classification']['excluded']=[{'code':'B004','source':'鉴定表第 4 行','excluded':True}]
        extra=core.empty_lot(p['template'],'手工新增');extra['fields']['图片文件夹名称']='4';extra['imageIds']=[p['files'][-1]['id']];p['lots'].append(extra)
        self.assertTrue(any(e['field']=='已排除标的' for e in core.validate(p)['errors']))
        with self.assertRaisesRegex(ValueError,'已排除标的'):core.export(p,True)

    def test_ai_cannot_split_or_reprice_explicit_grouping(self):
        p=self.project();s={'id':'source','fileId':p['files'][0]['id'],'name':'报价.txt','text':'B001-B002合拍设备2000，单件估价500，B001单件500。'}
        fact=lambda value:{'value':value,'source':'source','quote':s['text'],'raw':value,'unit':''}
        proposal={'records':[{'matchId':p['lots'][0]['id'],'fields':{'拍品名称':fact('B001-B002合拍设备'),'起拍价':fact('500')}},{'matchId':'','fields':{'拍品名称':fact('B001单件'),'起拍价':fact('500')}}],'shared':{}}
        issues=ai.apply_verified(p,proposal,[s],{'filled':0,'created':0,'rejected':0})
        self.assertEqual(len(p['lots']),2);self.assertEqual(p['lots'][0]['fields']['起拍价'],'2000');self.assertTrue(issues)

    def test_common_fields_and_explicit_rates_replace_imported_values(self):
        p=self.project();p['lots'][1]['fields']['保证金金额']='777'
        core.apply_common(p,{'fields':{'付款时间限制':'3','保留价_价格':'0'},'rates':{'保证金金额':'20','最小加价幅度_卖家指定':'1'}})
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'400');self.assertEqual(p['lots'][1]['fields']['保证金金额'],'600')
        self.assertEqual(p['lots'][0]['fields']['最小加价幅度_卖家指定'],'20')
        self.assertFalse(any(e['field']=='保留价_价格' for e in core.validate(p)['errors']))
        with self.assertRaises(ValueError):core.apply_common(p,{'rates':{'保证金金额':'-1'}})

if __name__=='__main__':unittest.main(verbosity=2)
