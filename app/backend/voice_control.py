"""Device-bound spoken selection of Gemini native audio voices."""
import json
import os
from pathlib import Path
import tempfile

# Google Gemini prebuilt voice catalog; Live native audio supports these voices.
# https://ai.google.dev/gemini-api/docs/live-api/capabilities#change-voice-and-language
VOICES = dict([
    ('Zephyr','Bright'),('Puck','Upbeat'),('Charon','Informative'),
    ('Kore','Firm'),('Fenrir','Excitable'),('Leda','Youthful'),
    ('Orus','Firm'),('Aoede','Breezy'),('Callirrhoe','Easy-going'),
    ('Autonoe','Bright'),('Enceladus','Breathy'),('Iapetus','Clear'),
    ('Umbriel','Easy-going'),('Algieba','Smooth'),('Despina','Smooth'),
    ('Erinome','Clear'),('Algenib','Gravelly'),('Rasalgethi','Informative'),
    ('Laomedeia','Upbeat'),('Achernar','Soft'),('Alnilam','Firm'),
    ('Schedar','Even'),('Gacrux','Mature'),('Pulcherrima','Forward'),
    ('Achird','Friendly'),('Zubenelgenubi','Casual'),('Vindemiatrix','Gentle'),
    ('Sadachbia','Lively'),('Sadaltager','Knowledgeable'),('Sulafat','Warm'),
])
_CANONICAL={name.casefold():name for name in VOICES}

VOICE_CONTROL_DECLARATION={
    'name':'voice_control',
    'description':(
        'List, inspect, change, or reset this robot\'s spoken Gemini voice when the user asks. '
        'Use list for available names and styles; get for the current voice; set with a supported '
        'voice name; reset for the original default voice. This changes the audible voice, '
        'not the intelligence model. Confirm a change only when ok is true, and explain that '
        'it takes effect on the next reply; the current confirmation keeps its existing voice. '
        'For an unspecified change, ask the user which voice or style they prefer. '
        'When listing, offer a few clear choices unless the user asks for the complete catalog.'
    ),
    'parameters':{
        'type':'object',
        'properties':{
            'action':{'type':'string','enum':['list','get','set','reset']},
            'voice':{'type':'string','enum':list(VOICES),'description':'Required for set.'},
        },
        'required':['action'],
    },
}

def normalize_live_voice(value,default='Umbriel'):
    return _CANONICAL.get(str(value or '').strip().casefold(),
                          _CANONICAL.get(str(default).casefold(),'Umbriel'))

def _read(path):
    data=json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data,dict) or any(not isinstance(v,dict) for v in data.values()):
        raise ValueError('Invalid settings structure')
    return data

def _save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,
                                         prefix='.voice-settings-',delete=False) as f:
            temporary=Path(f.name)
            json.dump(data,f,indent=2)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)

def control_voice(settings_path,device_id,args,default='Umbriel'):
    """The caller binds device_id from its authenticated conversation, never tool arguments."""
    path=Path(settings_path)
    if not isinstance(args,dict) or set(args)-{'action','voice'}:
        return {'ok':False,'error':'Unsupported voice request arguments.'}
    action=args.get('action')
    if action not in ('list','get','set','reset'):
        return {'ok':False,'error':'Choose list, get, set, or reset.'}
    if action=='set':
        selected=_CANONICAL.get(str(args.get('voice') or '').strip().casefold())
        if not selected:
            return {'ok':False,'error':'Unknown voice. Ask for the available voices; no change was made.'}
    else:selected=normalize_live_voice(default)
    try:data=_read(path)
    except (OSError,ValueError):
        return {'ok':False,'error':'Voice settings are unavailable; no change was made.'}
    row=dict(data.get(device_id) or {})
    current=normalize_live_voice(row.get('live_voice_name'),default)
    if action in ('get','list'):
        result={'ok':True,'voice':current,'style':VOICES[current],
                'voice_source':row.get('pixel_tts_voice','gemini'),
                'default_voice':normalize_live_voice(default)}
        if action=='list':result['voices']=[{'name':k,'style':v} for k,v in VOICES.items()]
        return result
    changed=row.get('live_voice_name')!=selected or row.get('pixel_tts_voice')!='gemini'
    if changed:
        row.update(live_voice_name=selected,pixel_tts_voice='gemini')
        data[device_id]=row
        try:_save(path,data)
        except OSError:
            return {'ok':False,'error':'The voice could not be saved; no change was made.'}
    return {'ok':True,'voice':selected,'style':VOICES[selected],'changed':changed,
            'applies':'next_reply','message':f'Saved {selected}. The next reply will use it.'}
