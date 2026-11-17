"""Conversation-local element handles, bounded accessibility snapshots and diffs."""
import difflib
import json
import math


class StaleRef(ValueError):
    code='stale_ref'
    hint='Take a new browser.snapshot and use a reference from that result.'


def changes(before,after,full=False):
    """Prefer changed lines unless navigation or a large change needs full context."""
    old=before.splitlines() if before else []
    new=after.splitlines()
    added=[]
    removed=[]
    for line in difflib.ndiff(old,new):
        if line.startswith('+ '):added.append(line[2:])
        elif line.startswith('- '):removed.append(line[2:])
    if full or not before or max(len(added),len(removed))>max(1,len(new)//2):
        return {'snapshot':after,'snapshot_kind':'full'}
    return {'changes':{'added':added,'removed':removed},'snapshot_kind':'diff'}


class BrowserRefs:
    def __init__(self):
        self.states={}
        self.serial=0

    def forget(self,cid):
        """Invalidate every reference when its owned page closes."""
        old=self.states.pop(cid,{})
        for item in old.get('refs',{}).values():
            try:item['element'].dispose()
            except Exception:pass

    def snapshot(self,cid,page,query=None,*,reset=True,full=False):
        """Refresh handles and return at most 150 controls plus bounded ARIA text."""
        if query is not None and (not isinstance(query,str) or len(query)>200):
            raise ValueError('filter must be text or role under 200 characters')
        doc=page.evaluate('''() => {
            if (!window.__musebridgeSnapshot) window.__musebridgeSnapshot={map:new WeakMap(),next:0,doc:crypto.randomUUID?crypto.randomUUID():String(Math.random())};
            return window.__musebridgeSnapshot.doc;
        }''')
        old=self.states.get(cid,{})
        if old.get('doc')!=doc:reset=True
        previous=old.get('text','')
        prior_by_id={} if reset else {item['id']:ref for ref,item in old.get('refs',{}).items()}
        collection=page.evaluate_handle('''q => Array.from(document.querySelectorAll(
            'button,input,a,select,textarea,[role],h1,h2,h3,label,p,summary')).filter(n=>{
                const r=n.getBoundingClientRect();
                const role=n.getAttribute('role')||({input:'textbox',textarea:'textbox',select:'combobox',a:'link'})[n.tagName.toLowerCase()]||n.tagName;
                const text=role+' '+(n.getAttribute('aria-label')||'')+' '+(n.innerText||'')+' '+(n.id||'');
                return r.width>0&&r.height>0&&(!q||text.toLowerCase().includes(q.toLowerCase()));
            }).slice(0,151)''',query)
        properties=collection.get_properties()
        controls=[]
        refs={}
        for key,handle in properties.items():
            if not key.isdigit():continue
            element=handle.as_element()
            if element is None:continue
            data=element.evaluate('''n => {
                const s=window.__musebridgeSnapshot;
                if(!s.map.has(n))s.map.set(n,++s.next);
                const tag=n.tagName.toLowerCase();
                const roles={button:'button',input:n.type==='checkbox'?'checkbox':'textbox',textarea:'textbox',a:'link',select:'combobox',summary:'button'};
                const r=n.getBoundingClientRect();
                return {id:s.map.get(n),tag,role:n.getAttribute('role')||roles[tag]||tag,
                    name:(n.getAttribute('aria-label')||Array.from(n.labels||[]).map(l=>l.innerText).join(' ')||n.innerText||n.getAttribute('placeholder')||n.id||'').slice(0,200),
                    value:n.type==='password'?'[private]':String(n.value||'').slice(0,200),
                    enabled:!n.disabled,rectangle:[r.x,r.y,r.width,r.height]};
            }''')
            if len(controls)>=150 or query and query.casefold() not in (data['role']+' '+data['name']).casefold():
                element.dispose()
                continue
            ref=prior_by_id.get(data['id'])
            if not ref:
                self.serial+=1
                ref='e'+str(self.serial)
            refs[ref]={'element':element,**data}
            controls.append({'ref':ref,**{k:v for k,v in data.items() if k!='id'}})
        collection.dispose()
        for item in old.get('refs',{}).values():
            try:item['element'].dispose()
            except Exception:pass
        aria=page.locator('body').aria_snapshot(timeout=5000)
        if query:aria='\n'.join(line for line in aria.splitlines() if query.casefold() in line.casefold())
        lines=['['+item['ref']+'] '+item['role']+' '+json.dumps(item['name'],ensure_ascii=False)+
               (' value='+json.dumps(item['value'],ensure_ascii=False) if item['value'] else '') for item in controls]
        text='\n'.join(lines)+'\nARIA:\n'+aria[:6000]
        self.states[cid]={'doc':doc,'refs':refs,'text':text,'url':page.url}
        truncated=len(properties)>150 or len(aria)>6000
        result={'title':page.title()[:1000],'url':page.url[:4096],'controls':controls,
                'text':text if reset else None,'truncated':truncated,
                **changes(previous,text,full=reset or full or old.get('url')!=page.url)}
        if truncated:result['hint']='Snapshot shortened. Use browser.snapshot(filter="text or role") to narrow it.'
        return result

    def act(self,cid,page,params):
        """Apply a richer action to the exact snapshotted node; stale nodes never retarget."""
        action=params.get('action')
        allowed={'click','fill','type','press','select','hover','scroll','wait_for','back','check','uncheck'}
        if action not in allowed:raise ValueError('Unsupported browser action; inspect browser.snapshot')
        state=self.states.get(cid,{})
        element=None
        ref=params.get('ref')
        if ref is not None:
            record=state.get('refs',{}).get(ref)
            if not record:raise StaleRef('Reference is stale or belongs to a different conversation')
            element=record['element']
            try:
                if not element.evaluate('n=>n.isConnected'):raise StaleRef('Referenced element was removed')
            except StaleRef:raise
            except Exception:raise StaleRef('Referenced page or element changed') from None
        elif params.get('selector'):
            element=page.locator(params['selector'])
        elif action not in ('back','scroll','wait_for'):
            raise ValueError('Provide a ref from browser.snapshot')
        text=params.get('text','')
        if not isinstance(text,str) or len(text)>10000:raise ValueError('text must be under 10000 characters')
        if action=='back':page.go_back(wait_until='domcontentloaded',timeout=15000)
        elif action=='wait_for':
            seconds=params.get('seconds')
            if seconds is not None:
                if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<=seconds<=10:raise ValueError('seconds must be 0..10')
                page.wait_for_timeout(seconds*1000)
            elif element is not None:element.wait_for_element_state('visible',timeout=10000)
            else:page.get_by_text(text,exact=False).first.wait_for(timeout=10000)
        elif action=='scroll':
            value=params.get('amount',params.get('value',500))
            if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>10000:raise ValueError('scroll value must be -10000..10000')
            if element is None:page.mouse.wheel(0,value)
            else:element.evaluate('(n,v)=>n.scrollBy(0,v)',value)
        elif action=='click':element.click(timeout=5000)
        elif action=='fill':element.fill(text,timeout=5000)
        elif action=='type':element.type(text,timeout=5000)
        elif action=='press':
            key=params.get('key')
            if not isinstance(key,str) or not key or len(key)>80:raise ValueError('key is required, for example Control+A')
            element.press(key,timeout=5000)
        elif action=='select':element.select_option(str(params.get('value',text)),timeout=5000)
        elif action=='hover':element.hover(timeout=5000)
        else:element.set_checked(action=='check',timeout=5000)
        return {'action':action,**self.snapshot(cid,page,reset=False)}
