"""Consolidated adapters sharing the existing owned resource managers."""
import json
import time
import uuid


NAMES = frozenset(('browser.navigate','browser.snapshot','browser.act','browser.eval',
    'desktop.launch','desktop.snapshot','terminal.open','terminal.send','terminal.close','mcp.list','screen.look'))


class PackTools:
    def __init__(self, router):
        self.router = router

    def call(self, name, cid, params, run_id, guard):
        router = self.router
        if name.startswith('browser.'):
            adapted = {**params, 'conversation_id': cid}
            return router.browser.call(name, adapted, guard=guard)
        if name == 'desktop.snapshot':
            return router.desktop.call(name,{**params,'conversation_id':cid,
                'window_handle':params.get('window',params.get('window_handle'))},guard=guard)
        if name == 'desktop.launch':
            from .desktop_launch import launch
            return launch(params['app_or_path'], params.get('args',[]), guard=guard)
        if name == 'mcp.list':
            return (router.mcp.tools(cid,params['server_id']) if params.get('server_id')
                    else {'servers':router.mcp.servers(cid)})
        if name == 'screen.look':
            source = params['source']
            if 'marks' in params and type(params['marks']) is not bool:
                raise ValueError('marks must be a boolean')
            if source == 'window':
                source = 'desktop'
                granted = router.vision.windows.get(router.execution.scope(cid))
                requested = params.get('window')
                if requested is not None and (not granted or requested != granted['window_handle']):
                    raise ValueError('Ask the owner to select this window in the local visual feedback controls')
            if source not in ('file','browser','desktop'):
                raise ValueError('source must be browser, window or file')
            result = router.vision.capture(router,cid,run_id,params.get('request_id',uuid.uuid4().hex),source,guard,
                path=params.get('path'),marks=params.get('marks',True),region=params.get('region'))
            return {**result, 'delivery':'image_turn',
                    'hint':'The image will arrive as your next message; end this turn now.'}
        if name == 'terminal.open':
            from .shells import resolve_shell
            argv = params.get('argv')
            if argv is None:
                shell, executable = resolve_shell(params.get('shell','powershell'))
                argv = [executable] + (['-NoLogo','-NoProfile'] if shell == 'powershell'
                    else ['--noprofile','--norc'] if shell == 'bash' else ['/d'])
            with router.execution.lock:
                guard()
                return router.terminals.start(cid,argv,params.get('cwd','.'),params.get('request_id',uuid.uuid4().hex),params.get('rows',24),params.get('cols',80))
        if name == 'terminal.close':
            return router.terminals.stop(cid,params['terminal_id'])
        if name == 'terminal.send':
            wait = params.get('wait_s',.25)
            if type(wait) not in (int,float) or not 0 <= wait <= 2:
                raise ValueError('wait_s must be 0..2 seconds')
            before = router.terminals.status(cid,params['terminal_id'])
            cursor = params.get('cursor', before['output_end'])
            with router.execution.lock:
                guard()
                receipt = router.terminals.write(cid,params['terminal_id'],params['text'],params.get('request_id',uuid.uuid4().hex))
            end = time.monotonic() + wait
            result = router.terminals.status(cid,params['terminal_id'],cursor)
            last_end, changed_at = result['output_end'], time.monotonic()
            while time.monotonic() < end:
                acknowledged = any(item['seq'] == receipt['seq'] and item['state'] not in ('pending','sending') for item in result['inputs'])
                if result['output_end'] != last_end:
                    last_end, changed_at = result['output_end'], time.monotonic()
                if acknowledged and (result['state'] not in ('accepted','running') or
                        result['output'] and time.monotonic() - changed_at >= .15):
                    break
                time.sleep(min(.025,max(0,end-time.monotonic())))
                result = router.terminals.status(cid,params['terminal_id'],cursor)
            latest = next((item for item in result['inputs'] if item['seq'] == receipt['seq']),None)
            if latest:receipt = {**receipt,'state':latest['state']}
            return {**result, 'input_receipt':receipt,
                    'hint':'Use terminal.read for additional output; pending input may not have been consumed yet.'}
        raise ValueError('Unknown consolidated adapter')
