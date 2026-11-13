"""Explicit local hook manifests. Never imports Python or repository code."""
import json
import re
from .hooks import EVENTS,MAX_COMMAND_CHARS


def parse_manifest(data):
    if not isinstance(data,bytes) or len(data)>65536:raise ValueError('Hook plugin must be a JSON file of at most 64 KiB')
    try:manifest=json.loads(data.decode('utf-8-sig'))
    except (UnicodeDecodeError,json.JSONDecodeError):raise ValueError('Invalid UTF-8 hook plugin JSON') from None
    if not isinstance(manifest,dict) or type(manifest.get('format_version')) is not int or manifest.get('format_version')!=1:raise ValueError('Unsupported hook plugin format; expected format_version 1')
    name=manifest.get('name');hooks=manifest.get('hooks')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',name):raise ValueError('Plugin name must be 1..30 letters, numbers, underscores or dashes')
    if not isinstance(hooks,list) or not 1<=len(hooks)<=8:raise ValueError('Plugin must contain 1..8 hook entries')
    selected=[];seen=set()
    for hook in hooks:
        if not isinstance(hook,dict):raise ValueError('Invalid hook entry')
        label=hook.get('name');event=hook.get('event');command=hook.get('command');tools=hook.get('tools',[]);timeout=hook.get('timeout_s',30)
        if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',label) or event not in EVENTS:raise ValueError('Invalid hook name or event')
        if not isinstance(command,str) or not command.strip() or len(command)>MAX_COMMAND_CHARS or '\x00' in command:raise ValueError('Invalid hook command')
        if not isinstance(tools,list) or len(tools)>50 or any(not isinstance(t,str) or not t or len(t)>100 for t in tools):raise ValueError('Invalid exact tool filter')
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 0<timeout<=300:raise ValueError('Plugin hook timeout must be 0..300 seconds')
        key=(name+'/'+label,event)
        if key in seen:raise ValueError('Duplicate hook in plugin')
        seen.add(key);selected.append({'name':key[0],'event':event,'command':command,'tools':tools,'timeout_s':timeout})
    return {'format_version':1,'name':name,'hooks':selected}


def install_disabled(service,scope,manifest):
    # Revalidate caller-provided structures before any database mutation.
    external={'format_version':1,'name':manifest['name'],'hooks':[{**h,'name':h['name'].split('/')[-1]} for h in manifest['hooks']]}
    manifest=parse_manifest(json.dumps(external).encode())
    current=service.list_hooks(scope);keys={(h['name'],h['event']) for h in current}
    if any((h['name'],h['event']) in keys for h in manifest['hooks']):raise ValueError('Plugin hooks already exist; edit them locally instead of overwriting')
    if len(current)+len(manifest['hooks'])>20:raise ValueError('Plugin exceeds the project hook limit')
    for event in EVENTS:
        if sum(h['event']==event for h in current)+sum(h['event']==event for h in manifest['hooks'])>8:raise ValueError('Plugin exceeds the per-event hook limit')
    return [service.set_hook(scope,h['name'],h['event'],h['command'],False,h['timeout_s'],tool_filter=h['tools']) for h in manifest['hooks']]
