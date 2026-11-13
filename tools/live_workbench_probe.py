"""Actual Muse queue/MCP/terminal/hook/Git journey in disposable state."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from PySide6.QtCore import QLockFile
from musebridge.app import Worker
from musebridge.storage import Store
from musebridge.workspace import Workspace


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before the workbench probe')
    root=Path(__file__).resolve().parents[1]/'.local'/('workbench-probe-'+str(int(time.time())))
    root.mkdir(parents=True)
    def git(*args):
        return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode().strip()
    git('init');git('config','user.name','MuseBridge fixture');git('config','user.email','fixture@localhost')
    (root/'input.txt').write_text('seed',encoding='utf-8');git('add','input.txt');git('-c','core.hooksPath=NUL','commit','-m','fixture')
    branch_before=git('branch','--show-current');(root/'input.txt').write_text('owner-dirty',encoding='utf-8')
    (root/'owner-only.txt').write_text('preserve me',encoding='utf-8')
    (root/'interactive_fixture.py').write_text("from pathlib import Path\nprint('READY_FOR_LINE',flush=True)\nvalue=input()\nPath('terminal-result.txt').write_text(value,encoding='utf-8')\nprint('INPUT_RECORDED',flush=True)\n",encoding='utf-8')
    worker=Worker(store);catalog=worker.catalog;router=worker.router
    project=catalog.create_project('Disposable workbench acceptance',root,
        'Disposable test. Do not modify personal memory, other projects, services or devices. Preserve owner-dirty input.txt and owner-only.txt.')
    cid=catalog.create_conversation('Queued workbench integration',project['id'])['id'];worker.select(cid)
    fixture=Path(__file__).resolve().parent/'mcp_fixture_server.py'
    server=router.mcp.configure_server(cid,'Synthetic local MCP fixture',sys.executable,[str(fixture)],grants={'echo':'read'},enabled=True)
    router.hooks.set_hook('project:'+project['id'],'fixture-log','after_file_edit',"Add-Content -LiteralPath hook.log -Value edited",enabled=True,timeout_s=20)
    worker.connect();report={}
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            core=worker.core
            if core and core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            if worker.future and worker.future.done():raise RuntimeError('Connection ended')
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection readiness timeout')
        print('Connected; queueing two explicit fixture requests',flush=True)
        worker.queue.add(cid,'Complete this bounded Windows workbench fixture through actual registered tools. '
            'Keep all original files intact. Call bridge.context first and use the current run_id/scope_revision for mutations. '
            '1. Discover the MCP server '+server+' with mcp.tools; call mcp.read echo with arguments={"text":"integration"}. '
            'Create mcp-result.txt containing exactly the text result. '
            '2. Start terminal.start with request_id=interactive-fixture, argv='+json.dumps([sys.executable,'-u','interactive_fixture.py'])+'. '
            'Use terminal.read until READY_FOR_LINE, then terminal.write request_id=one-line with text="fixture-ok\\r" '
            '(a literal carriage return, not backslash and r). Read until the fixture exits and verify terminal-result.txt contains fixture-ok. '
            '3. Call git.status and git.diff. Create exactly one fresh worktree with git.worktree.create branch=probe/isolated, default base HEAD. '
            'Do not switch to or operate in the new conversation; leave it for the owner. '
            '4. Create queue-one.txt containing exactly first-done using file.write create=true. '
            'Wait for any returned after-edit hook jobs and inspect exit codes. Finally plan.update state=complete. '
            'Do not use SSH, other devices, external services or personal memory. Use actual tools, not claims.')
        worker.queue.add(cid,'This is the second explicitly queued fixture request. Use the new current run_id/scope_revision. '
            'Read queue-one.txt and verify it is first-done. Create queue-two.txt containing exactly second-done. '
            'Record one project.note kind=progress text="Workbench fixture completed" source="disposable acceptance test". '
            'Inspect project.handoff. Wait for any returned after-edit hook jobs, then plan.update state=complete. Do not modify original files or use other devices/services.')
        deadline=time.monotonic()+420
        while time.monotonic()<deadline:
            entries=worker.queue.rows(cid)
            completed=(len(entries)==2 and all(e['run_id'] and router.execution.run(cid,e['run_id'])['state'] in ('remote_finished','remote_reply_finished') for e in entries))
            if completed and (root/'queue-two.txt').exists() and catalog.context(cid)['goal_state']=='complete':break
            if any(e['state']=='unknown' for e in entries):raise RuntimeError('Queue held an uncertain send')
            await asyncio.sleep(.5)
        entries=worker.queue.rows(cid);calls=catalog.rows('SELECT tool,state FROM tool_calls WHERE conversation_id=?',(cid,))
        tools={c['tool'] for c in calls};terminals=router.terminals.list(cid)
        managed=router.git.worktrees(cid)['managed']
        worktree_usable=bool(managed and Workspace(managed[0]['worktree_project_root']).read('input.txt')['text']=='seed')
        original=(root/'input.txt').read_text()=='owner-dirty' and (root/'owner-only.txt').read_text()=='preserve me' and git('branch','--show-current')==branch_before
        def equals(name,text):return (root/name).is_file() and (root/name).read_text(encoding='utf-8').strip()==text
        ordered=False
        if len(entries)==2 and all(e['run_id'] for e in entries):
            first=router.execution.run(cid,entries[0]['run_id']);second=router.execution.run(cid,entries[1]['run_id'])
            ordered=bool(first['state'] in ('remote_finished','remote_reply_finished') and first['finished'] and second['created']>=first['finished'])
        report={'conversation_id':cid,'queue_delivered':len(entries)==2 and all(e['state']=='acknowledged' for e in entries),
            'queue_waited_for_first_terminal':ordered,'queued_files_verified':equals('queue-one.txt','first-done') and equals('queue-two.txt','second-done'),
            'mcp_result_verified':equals('mcp-result.txt','fixture:integration') and 'mcp.read' in tools,
            'interactive_terminal_verified':equals('terminal-result.txt','fixture-ok') and any(t['state']=='succeeded' for t in terminals),
            'hook_effect_verified':(root/'hook.log').is_file() and any(i['state']=='succeeded' for i in router.hooks.refresh(cid)),
            'worktree_usable':worktree_usable,'original_work_preserved':original,
            'continuity_note_recorded':bool(router.continuity.list(cid)['notes']),'handoff_called':'project.handoff' in tools,
            'all_answers_complete':all(e['run_id'] and router.execution.run(cid,e['run_id'])['state'] in ('remote_finished','remote_reply_finished') for e in entries),
            'unknown_calls':sum(c['state']=='unknown' for c in calls)}
        report['passed']=all(report[k] for k in ('queue_delivered','queue_waited_for_first_terminal','queued_files_verified','mcp_result_verified','interactive_terminal_verified','hook_effect_verified','worktree_usable','original_work_preserved','continuity_note_recorded','handoff_called'))
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        worker.close();lock.unlock();(root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True);print('Sanitized report: '+str(root/'report.json'),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
