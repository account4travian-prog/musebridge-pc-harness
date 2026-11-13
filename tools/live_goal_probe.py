"""Two-turn, read-only goal continuation test in the disposable Muse conversation."""
import asyncio
import json
from pathlib import Path
from PySide6.QtCore import QLockFile
from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.goals import GoalRunner
from musebridge.storage import Store
from musebridge.tooling import ToolRouter


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before goal probe')
    catalog=Catalog(store.directory)
    row=catalog.rows("SELECT * FROM conversations WHERE title='Harness integration test' ORDER BY created DESC LIMIT 1")[0]
    cid=row['id'];router=ToolRouter(catalog,store.directory)
    core=Core(store,lambda s:print(s,flush=True),lambda *a:None,lambda _:None,
              catalog=catalog,router=router,conversation_id=cid)
    async def send(cid,prompt):return await core.send(prompt)
    goal=GoalRunner(catalog,send,lambda cid,text:print(text,flush=True));core.event_observer=goal.feed
    connection=asyncio.create_task(core.run())
    report={}
    try:
        for _ in range(150):
            if core.session and core.session.registered.is_set() and core._opened.is_set():break
            await asyncio.sleep(.2)
        else:raise TimeoutError('Control not ready')
        catalog.plan(cid,goal='Perform a two-turn read-only harness test. On the FIRST continuation turn, '
            'read probe.txt and verify its text is verified, then plan.update to record PHASE_ONE_DONE with '
            'state active and finish the turn. Do not mark complete in that first turn. On the SECOND '
            'continuation turn, read probe.txt again, then plan.update to record BOTH_PHASES_DONE with '
            'state complete. Do not modify files or personal/global memory.',
            text='PHASE_ONE_PENDING; PHASE_TWO_PENDING',state='active')
        result=await goal.run(cid,max_rounds=2,turn_timeout_s=120)
        context=catalog.context(cid)
        report={'result':result,'plan_state':context['goal_state'],'plan':context['plan'],
                'passed':context['goal_state']=='complete' and 'BOTH_PHASES_DONE' in context['plan']}
    except Exception as exc:report={'passed':False,'error':type(exc).__name__}
    finally:
        await goal.stop();core.stop.set()
        try:await asyncio.wait_for(connection,8)
        except (Exception,asyncio.CancelledError):
            connection.cancel();await asyncio.gather(connection,return_exceptions=True)
        router.close();catalog.close();lock.unlock()
    path=Path(__file__).resolve().parents[1]/'.local'/'goal-probe-report.json'
    path.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
