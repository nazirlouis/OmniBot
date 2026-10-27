import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from voice_control import control_voice,normalize_live_voice,VOICES

class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'bot_settings.json'
        self.data={'waveshare-143c':{'model':'preserved-model','custom':'keep','pixel_tts_voice':'gemini'},
                   'another-bot':{'live_voice_name':'Kore','custom':{'keep':True}}}
        self.path.write_text(json.dumps(self.data))
    def tearDown(self):self.tmp.cleanup()
    def call(self,action,**kw):return control_voice(self.path,'waveshare-143c',dict(action=action,**kw))
    def test_read_only_catalog(self):
        before=self.path.read_bytes();r=self.call('list')
        self.assertEqual(len(r['voices']),30);self.assertEqual(r['voice'],'Umbriel')
        self.assertEqual(self.path.read_bytes(),before)
    def test_persistence_scope_and_engine(self):
        r=self.call('set',voice='puck');self.assertTrue(r['ok']);self.assertEqual(r['applies'],'next_reply')
        saved=json.loads(self.path.read_text());self.assertEqual(saved['another-bot'],self.data['another-bot'])
        self.assertEqual(saved['waveshare-143c']['custom'],'keep');self.assertEqual(saved['waveshare-143c']['model'],'preserved-model')
        self.assertEqual(saved['waveshare-143c']['pixel_tts_voice'],'gemini')
        code='from voice_control import control_voice;import sys;print(control_voice(sys.argv[1],"waveshare-143c",{"action":"get"})["voice"])'
        r=subprocess.check_output([sys.executable,'-c',code,str(self.path)],cwd=Path(__file__).resolve().parents[1],text=True)
        self.assertEqual(r.strip(),'Puck')
        self.assertEqual(self.call('reset')['voice'],'Umbriel')
        self.assertEqual(self.call('get')['voice'],'Umbriel')
    def test_invalid_request_and_scope(self):
        before=self.path.read_bytes()
        for args in [dict(action='set',voice='invented'),dict(action='set'),dict(action='delete'),dict(action='set',voice='Puck',device_id='another-bot')]:
            self.assertFalse(control_voice(self.path,'waveshare-143c',args)['ok'])
            self.assertEqual(self.path.read_bytes(),before)
    def test_write_failure_and_corruption(self):
        before=self.path.read_bytes()
        with patch('voice_control.os.replace',side_effect=OSError('fixture')):
            self.assertFalse(self.call('set',voice='Puck')['ok'])
        self.assertEqual(self.path.read_bytes(),before);self.assertFalse(list(self.path.parent.glob('.voice-settings-*')))
        self.path.write_text('{bad-json');self.assertFalse(self.call('set',voice='Puck')['ok'])
        self.assertEqual(self.path.read_text(),'{bad-json')
    def test_default_override(self):
        self.assertEqual(normalize_live_voice(None,'Kore'),'Kore')
        self.assertEqual(normalize_live_voice('invalid','bad-default'),'Umbriel')

def integration():
    with tempfile.TemporaryDirectory() as data:
        os.environ['OMNIBOT_DATA_DIR']=data
        root=Path(__file__).resolve().parents[1]
        sys.path.insert(0,str(root))
        import app
        from google.genai import types
        app.resolve_effective_system_instruction=lambda *a,**kw:'Fixture assistant'
        app.history_by_device['waveshare-143c']=['preserve-history']
        async def run():
            responses=await app.execute_live_pixel_tool_calls('waveshare-143c','fixture',[
                types.FunctionCall(name='voice_control',args={'action':'set','voice':'Kore'},id='voice-1')])
            assert responses[0].response['result']['ok']
            assert app.history_by_device['waveshare-143c']==['preserve-history']
            app.history_by_device.clear()
            config=app._build_live_coordinator('waveshare-143c')._live_config()
            assert config.speech_config.voice_config.prebuilt_voice_config.voice_name=='Kore'
            names=[f.name for tool in config.tools for f in (tool.function_declarations or [])]
            assert 'voice_control' in names
            await app.update_settings('waveshare-143c',app.BotSettingsSchema(model='fixture-model'))
            assert app.get_bot_settings('waveshare-143c')['live_voice_name']=='Kore'
        asyncio.run(run())
        print('PASS: actual tool dispatch, current-session history preservation, next-session voice, declared tool, legacy settings-form preservation')

if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(VoiceTests))
    if not result.wasSuccessful():sys.exit(1)
    integration()
