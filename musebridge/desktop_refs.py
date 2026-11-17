"""UIA runtime-identity references and window-scoped snapshot differences."""
import json
import time

from .browser_refs import changes


class DesktopStaleRef(ValueError):
    code='stale_ref'
    hint='Take a new desktop.snapshot for this window and use its reference.'


def identity(control):
    """Use UIA runtime IDs rather than mutable tree positions."""
    value=getattr(control.element_info,'runtime_id',None)
    if callable(value):value=value()
    return tuple(value) if value else ('fixture',id(control))


def key_chord(text):
    """Convert a bounded chord into pywinauto syntax without accepting raw scripts."""
    if not isinstance(text,str) or len(text)>80:raise ValueError('keys requires a chord such as ctrl+a')
    parts=[part.strip().casefold() for part in text.split('+')]
    if len(parts)>5 or not parts or not parts[-1]:raise ValueError('Invalid key chord')
    modifiers={'ctrl':'^','control':'^','alt':'%','shift':'+','win':'{VK_LWIN}'}
    if any(part not in modifiers for part in parts[:-1]):raise ValueError('Unsupported key modifier')
    if ('alt' in parts and parts[-1]=='f4') or ('win' in parts and parts[-1]=='l'):
        raise ValueError('Closing or locking the desktop with this chord is refused')
    key=parts[-1]
    names={'enter':'ENTER','return':'ENTER','tab':'TAB','escape':'ESC','esc':'ESC',
           'backspace':'BACKSPACE','delete':'DELETE','left':'LEFT','right':'RIGHT',
           'up':'UP','down':'DOWN','home':'HOME','end':'END','pageup':'PGUP','pagedown':'PGDN','space':'SPACE'}
    if len(key)==1 and key.isascii() and key.isalnum():suffix=key
    elif key in names:suffix='{'+names[key]+'}'
    elif key.startswith('f') and key[1:].isdigit() and 1<=int(key[1:])<=12:suffix='{'+key.upper()+'}'
    else:raise ValueError('Unsupported key; use a letter, digit or named navigation key')
    return ''.join(modifiers[part] for part in parts[:-1])+suffix


class DesktopRefs:
    def __init__(self,tools):
        self.tools=tools
        self.states={}
        self.serial=0

    def snapshot(self,cid,hwnd,window,query=None,reset=True):
        """Capture controls, values, focus, and runtime identities, capped by the UIA tree."""
        if query is not None and (not isinstance(query,str) or len(query)>200):raise ValueError('filter must be under 200 characters')
        nodes,wrappers,truncated=self.tools._tree(window)
        old=self.states.get((cid,hwnd),{})
        pid=int(window.process_id())
        if old.get('pid')!=pid:reset=True
        previous={} if reset else {record['identity']:ref for ref,record in old.get('refs',{}).items()}
        refs={}
        output=[]
        focused=None
        for node in nodes:
            control=wrappers[node['selector']]
            if query and query.casefold() not in (node['control_type']+' '+node['name']).casefold():continue
            ident=identity(control)
            ref=previous.get(ident)
            if not ref:
                self.serial+=1
                ref='e'+str(self.serial)
            record={**node,'ref':ref}
            try:
                if control.has_keyboard_focus():focused=ref
            except Exception:pass
            if node['control_type']=='Edit':
                try:
                    info=control.element_info
                    private=getattr(info,'is_password',False) or getattr(getattr(info,'element',None),'CurrentIsPassword',False)
                    record['value']='[private]' if private else str(control.get_value())[:256]
                except Exception:record['value']=node['name'][:256]
            refs[ref]={'identity':ident,'node':record}
            output.append(record)
        text='\n'.join('['+node['ref']+'] '+node['control_type']+' '+json.dumps(node['name'],ensure_ascii=False)+
                       (' value='+json.dumps(node['value'],ensure_ascii=False) if 'value' in node else '')+
                       (' enabled' if node['enabled'] else ' disabled') for node in output)
        self.states[(cid,hwnd)]={'pid':pid,'refs':refs,'text':text}
        result={'window':{'window_handle':hwnd,'process_id':pid,'title':window.window_text()[:300],
                          'rectangle':self.tools._window_rect(hwnd,window)},'controls':output,'count':len(output),
                'focused':focused,'truncated':truncated,**changes(old.get('text',''),text,full=reset)}
        if len(nodes)<5 and self.tools.ocr_enabled:
            try:
                from .windows_ocr import recognize
                capture=self.tools._screenshot(cid,hwnd,pid)
                result['ocr']=recognize(capture['path'])
            except Exception:
                result['ocr_note']='No readable OCR fallback; use screen.look for visual inspection.'
        return result

    def resolve(self,cid,hwnd,window,ref):
        """Resolve the same runtime element, refusing removed/replaced controls and reused HWNDs."""
        state=self.states.get((cid,hwnd),{})
        record=state.get('refs',{}).get(ref)
        if not record or state.get('pid')!=int(window.process_id()):
            raise DesktopStaleRef('Reference is stale or belongs to another conversation/window')
        nodes,wrappers,_=self.tools._tree(window)
        for node in nodes:
            control=wrappers[node['selector']]
            if identity(control)==record['identity']:
                return control,node
        raise DesktopStaleRef('The referenced UIA element was removed or replaced')

    def act(self,cid,hwnd,pid,window,params):
        """Perform one guarded action; wait loops remain cancellable without mutation locks."""
        action=params['action']
        guard=params.get('_guard') or (lambda:None)
        if action=='wait_for':
            deadline=time.monotonic()+10
            text=params.get('text','')
            while True:
                guard()
                current=self.snapshot(cid,hwnd,window,reset=False)
                if any(text.casefold() in node['name'].casefold() for node in current['controls']):return current
                if time.monotonic()>=deadline:raise TimeoutError('Element text did not appear within 10 seconds')
                time.sleep(.1)
        control=None
        node=None
        if params.get('ref'):
            control,node=self.resolve(cid,hwnd,window,params['ref'])
        elif 'x' not in params:
            _,control,node=self.tools._select_control(window,params)
        if node is not None and (not node['visible'] or not node['enabled']):raise ValueError('Selected control is hidden or disabled')
        foreground=self.tools._foreground()
        before_windows={item['window_handle'] for item in self.tools._process_windows(pid)}
        lock=getattr(self.tools,'execution_lock',None)
        import contextlib
        with lock if lock is not None else contextlib.nullcontext():
            guard()
            self.tools._guard_mutation(hwnd,pid,action,foreground)
            if control is None:
                x,y=params['x'],params['y']
                rect=self.tools._window_rect(hwnd,window)
                if not rect or not rect['left']<=x<rect['right'] or not rect['top']<=y<rect['bottom']:raise ValueError('Coordinates must be inside this window')
                from pywinauto import mouse
                if action not in ('click','double_click','right_click'):raise ValueError('Coordinates support pointer actions only')
                mouse.click(button='right' if action=='right_click' else 'left',coords=(x,y)) if action!='double_click' else mouse.double_click(coords=(x,y))
            elif action in ('invoke','click'):
                method=getattr(control,'invoke',None)
                if callable(method):
                    try:method()
                    except Exception as exc:
                        if type(exc).__name__!='NoPatternInterfaceError':raise
                        guard()
                        self.tools._guard_mutation(hwnd,pid,action,foreground)
                        control.click_input()
                else:
                    guard()
                    self.tools._guard_mutation(hwnd,pid,action,foreground)
                    control.click_input()
            elif action=='focus':control.set_focus()
            elif action=='set_text':
                if node['control_type']!='Edit':raise ValueError('set_text is supported only for Edit controls')
                control.set_edit_text(params['text'])
            elif action in ('double_click','right_click'):
                control.click_input(button='right' if action=='right_click' else 'left',double=action=='double_click')
            elif action in ('select','toggle','expand'):
                method='collapse' if action=='expand' and params.get('value') is False else action
                getattr(control,method)()
            elif action=='scroll':
                direction=params.get('direction','down')
                if direction not in ('up','down','left','right'):raise ValueError('Invalid scroll direction')
                control.scroll(direction,'line',count=3)
            elif action in ('keys','type'):
                from pywinauto.keyboard import send_keys
                keys=key_chord(params.get('keys',params.get('text',''))) if action=='keys' else ''.join(
                    '{'+char+'}' if char in '+^%~(){}' else char for char in params['text'])
                control.set_focus()
                try:
                    guard()
                    self.tools._guard_mutation(hwnd,pid,action,foreground)
                except Exception as exc:
                    # Focusing a control already had an effect, even if keys were refused.
                    if hasattr(exc,'no_effect'):exc.no_effect=False
                    raise
                send_keys(keys,with_spaces=True,with_tabs=True,with_newlines=True,vk_packet=True)
            else:raise ValueError('Unsupported desktop action')
        result=self.snapshot(cid,hwnd,window,reset=False)
        result['new_windows']=[item for item in self.tools._process_windows(pid) if item['window_handle'] not in before_windows]
        return {'ok':True,'action':action,'window_handle':hwnd,'verified':False,
                'verification_required':'Inspect the snapshot changes to verify the effect.',**result}
