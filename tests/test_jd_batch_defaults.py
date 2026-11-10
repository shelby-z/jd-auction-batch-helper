from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import sys,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.codex-work/jd-batch/python-deps')]
import jd_batch as core
import jd_batch_ai as ai
from test_jd_batch import picture
from test_jd_batch_classification import fixture

class DefaultsTests(unittest.TestCase):
    def setUp(self):core.DATA=ROOT/'.codex-work/jd-batch/default-tests'
    def project(self,prices=('900','200','2000')):
        p=core.new_project('统一设置合成测试','government')
        for i,price in enumerate(prices,1):
            lot=core.empty_lot('government',f'设备{i}');lot['fields'].update({'起拍价':price,'图片文件夹名称':str(i),'保证金金额':'17'})
            p['lots'].append(lot)
        return p
    def rules(self,p,rate='20',minimum='300'):
        core.apply_common(p,{'fields':{'联系人':'测试联系人'},'rates':{'保证金金额':rate,'最小加价幅度_卖家指定':'1'},'depositMinimum':minimum})
    def edit(self,p,index,key,value):
        body=deepcopy(p);body['lots'][index]['fields'][key]=value;core.update_review(p,body)
    def deposits(self,p):return [l['fields']['保证金金额'] for l in p['lots']]

    def test_user_examples_threshold_cap_and_rounding(self):
        p=self.project(('900','200','2000','1500','333.33'));self.rules(p)
        self.assertEqual(self.deposits(p),['300','200','400','300','300'])
        self.rules(p,minimum='');self.assertEqual(self.deposits(p)[-1],'66.67')

    def test_shared_rules_never_change_original_starting_prices(self):
        prices=['16.7','240','1210'];p=self.project(prices)
        core.apply_common(p,{'fields':{'起拍价':'900'},'rates':{'保证金金额':'20','最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':True,'incrementMinimum':'5'})
        self.assertEqual([l['fields']['起拍价'] for l in p['lots']],prices)
        self.assertEqual(self.deposits(p),['3.34','48','242'])
        self.assertEqual([l['fields']['最小加价幅度_卖家指定'] for l in p['lots']],['5','5','10'])
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'2'},'incrementMinimumEnabled':False})
        self.assertEqual([l['fields']['起拍价'] for l in p['lots']],prices)

    def test_increment_threshold_wins_over_five_and_integer_rounding(self):
        p=self.project(('240','1210','260','1750'))
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':True,'incrementMinimum':'5'})
        self.assertEqual(p['incrementOptimization'],{'roundInteger':True,'multipleFive':True})
        self.assertEqual([l['fields']['最小加价幅度_卖家指定'] for l in p['lots']],['5','10','5','20'])
        # The threshold need not itself be an integer or multiple of five.
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':True,'incrementMinimum':'6.5'})
        self.assertEqual(p['lots'][0]['fields']['最小加价幅度_卖家指定'],'6.5')

    def test_increment_switches_and_manual_exception(self):
        p=self.project(('240','1210'))
        args={'rates':{'最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':False,'incrementOptimization':{'roundInteger':True,'multipleFive':False}}
        core.apply_common(p,args);self.assertEqual([l['fields']['最小加价幅度_卖家指定'] for l in p['lots']],['2','12'])
        args['incrementOptimization']={'roundInteger':False,'multipleFive':False};core.apply_common(p,args)
        self.assertEqual(p['lots'][0]['fields']['最小加价幅度_卖家指定'],'2.4')
        self.edit(p,0,'最小加价幅度_卖家指定','8');args.update(incrementMinimumEnabled=True,incrementMinimum='5');core.apply_common(p,args)
        self.assertEqual(p['lots'][0]['fields']['最小加价幅度_卖家指定'],'8')

    def test_increment_never_rounds_to_zero_even_without_custom_threshold(self):
        p=self.project(('240','1','0.01'))
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'}})
        self.assertEqual([l['fields']['最小加价幅度_卖家指定'] for l in p['lots']],['5','5','5'])
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':True,'incrementMinimum':'2'})
        self.assertEqual(p['lots'][0]['fields']['最小加价幅度_卖家指定'],'5')
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'},'incrementMinimumEnabled':False,'incrementOptimization':{'roundInteger':True,'multipleFive':False}})
        self.assertEqual(p['lots'][-1]['fields']['最小加价幅度_卖家指定'],'1')
        core.apply_common(p,{'rates':{'最小加价幅度_卖家指定':'1'},'incrementOptimization':{'roundInteger':False,'multipleFive':False}})
        self.assertEqual(p['lots'][-1]['fields']['最小加价幅度_卖家指定'],'0.01')

    def test_price_recalculation_exception_restore_and_new_lot(self):
        p=self.project();self.rules(p);self.edit(p,0,'起拍价','2000')
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'400')
        self.edit(p,0,'保证金金额','333');self.rules(p,rate='30')
        self.assertEqual(self.deposits(p),['333','200','600'])
        body=deepcopy(p);body['lots'][0]['inheritFields']=['保证金金额'];core.update_review(p,body)
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'600')
        body=deepcopy(p);lot=core.empty_lot('government','新增设备');lot['fields']['起拍价']='1000';body['lots'].append(lot);core.update_review(p,body)
        self.assertEqual(p['lots'][-1]['fields']['保证金金额'],'300');self.assertEqual(p['lots'][-1]['fields']['联系人'],'测试联系人')

    def test_individual_empty_is_preserved(self):
        p=self.project();self.rules(p);self.edit(p,0,'保证金金额','');self.rules(p,rate='10')
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'')
        self.assertIn('保证金金额',p['lots'][0]['commonOverrides'])

    def test_manual_before_defaults_is_preserved(self):
        p=self.project();p['lots'][0]['fields']['保证金金额']='777';p['lots'][0]['sources']['保证金金额']='人工核对'
        self.rules(p);self.assertEqual(self.deposits(p),['777','200','400'])

    def test_legacy_common_values_resume_but_differences_stay(self):
        p=self.project(('1000','1000'));p['priceRates']={'保证金金额':'20'}
        for lot,amount in zip(p['lots'],['200','777']):lot['fields']['保证金金额']=amount;lot['sources']['保证金金额']='人工核对'
        self.rules(p,rate='30',minimum='');self.assertEqual(self.deposits(p),['300','777'])

    def test_invalid_price_does_not_leave_stale_calculation(self):
        p=self.project();self.rules(p);self.edit(p,0,'起拍价','')
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'')
        self.edit(p,0,'起拍价','123.45');self.assertEqual(p['lots'][0]['fields']['保证金金额'],'123.45')

    def test_fixed_mode_and_disabling_then_reenabling(self):
        p=self.project();core.apply_common(p,{'fields':{'保证金金额':'88','最小加价幅度_卖家指定':'10'},'rates':{}})
        self.edit(p,0,'起拍价','1000');self.assertEqual(self.deposits(p),['88','88','88'])
        core.apply_common(p,{'fields':{},'rates':{}});self.edit(p,0,'起拍价','2000');self.assertEqual(p['lots'][0]['fields']['保证金金额'],'88')
        self.rules(p);self.assertEqual(p['lots'][0]['fields']['保证金金额'],'400')

    def test_invalid_threshold_and_amount_are_atomic(self):
        p=self.project();before=deepcopy(p)
        for minimum in ['-1','abc','300.001','NaN']:
            with self.assertRaises(ValueError):self.rules(p,minimum=minimum)
            self.assertEqual(p,before)
        with self.assertRaises(ValueError):core.apply_common(p,{'fields':{'保证金金额':'-1'}})
        with self.assertRaises(ValueError):core.apply_common(p,{'depositMinimum':'300','rates':{}})

    def test_queued_save_cannot_restore_stale_calculated_amount(self):
        p=self.project();self.rules(p);first=deepcopy(p);second=deepcopy(p)
        first['lots'][0]['fields']['起拍价']='2000';first['lots'][0]['editedFields']=['起拍价']
        second['lots'][0]['fields']['联系人']='排队修改';second['lots'][0]['editedFields']=['联系人']
        core.update_review(p,first);core.update_review(p,second)
        self.assertEqual(p['lots'][0]['fields']['起拍价'],'2000');self.assertEqual(p['lots'][0]['fields']['保证金金额'],'400')
        self.assertEqual(p['lots'][0]['fields']['联系人'],'排队修改');self.assertNotIn('保证金金额',p['lots'][0]['commonOverrides'])

    def test_ai_does_not_replace_defaults(self):
        p=self.project();self.rules(p);source={'id':'s','fileId':'file','name':'说明.txt','text':'设备1，保证金1000元'}
        fact=lambda value:{'value':value,'source':'s','quote':source['text'],'raw':value,'unit':''}
        ai.apply_verified(p,{'records':[{'matchId':p['lots'][0]['id'],'fields':{'拍品名称':fact('设备1'),'保证金金额':fact('1000')}}],'shared':{}},[source],{'filled':0,'created':0,'rejected':0})
        self.assertEqual(p['lots'][0]['fields']['保证金金额'],'300');self.assertNotIn('保证金金额',p['lots'][0]['conflicts'])

    def test_photos_use_metadata_only_and_legacy_ocr_is_not_sent(self):
        p=core.new_project('图片仅元数据','government');core.add_file(p,'无编号目录/照片.png',picture())
        with patch.object(core,'ocr_image',side_effect=AssertionError('照片不得 OCR')):core.analyze(p,True)
        photo=p['files'][0];self.assertGreater(photo['imageInfo']['width'],0);self.assertEqual(photo['text'],'')
        photo['text']='历史照片OCR不得发送';self.assertEqual(list(ai.chunks(p)),[])

    def test_schema_request_does_not_resend_entire_photo_inventory(self):
        p=core.new_project('表格和文件名','government');core.add_file(p,'安排.xls',fixture(True));core.add_file(p,'B001/原照片.png',picture());core.analyze(p,False)
        captured=[]
        def model(prompt,payload):captured.append(payload);return {'tables':[],'issues':[]}
        ai.recognize_grouping(p,model)
        self.assertEqual(len(captured),2);self.assertNotIn('imageFolders',captured[0])
        self.assertEqual(p['files'][1]['name'],'B001/原照片.png');self.assertGreater(p['files'][1]['imageInfo']['width'],0)

if __name__=='__main__':unittest.main(verbosity=2)
