import time
from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


def test_actual_check_job_binds_inputs_and_invalidates_goal_after_owner_edit(tmp_path):
    root=tmp_path/'project';root.mkdir();source=root/'input.txt';source.write_text('check-me')
    catalog=Catalog(tmp_path/'state');project=catalog.create_project('Checks',root)
    cid=catalog.create_conversation('Task checks',project['id'])['id'];router=ToolRouter(catalog,tmp_path/'state')
    def call(name,**params):
        result=router.call(name,{'conversation_id':cid,**params})
        assert result['ok'],result
        return result['payload']
    try:
        task=call('task.create',title='Verify the input',criteria='Run the input check')['task_id']
        call('task.update',task_id=task,state='active')
        job=call('job.start',request_id='check-input',command="if ((Get-Content -Raw input.txt) -ne 'check-me') { exit 3 }; Write-Output 'checked'",
            verification_task_id=task,verification_paths=['input.txt'])
        deadline=time.monotonic()+10
        while job['state'] in ('accepted','running') and time.monotonic()<deadline:job=call('job.wait',job_id=job['job_id'],timeout_s=1)
        assert job['exit_code']==0
        receipt=call('task.verify_job',task_id=task,job_id=job['job_id'],paths=['input.txt'])
        assert receipt['passed'] and receipt['evidence']['file_bound']
        assert call('task.update',task_id=task,state='complete')['state']=='complete'
        assert call('plan.update',state='complete')['goal_state']=='complete'
        source.write_text('changed after the check')
        rows=call('task.list')['tasks']
        assert rows[0]['state']=='awaiting_review'
        assert catalog.context(cid)['goal_state']=='paused'
        assert catalog.context(cid)['goal_verification_stale']
        assert not router.call('plan.update',{'conversation_id':cid,'state':'complete'})['ok']
    finally:router.close();catalog.close()


def test_manual_acceptance_is_local_and_read_only_policy_applies(tmp_path):
    catalog=Catalog(tmp_path);router=ToolRouter(catalog,tmp_path)
    try:
        task=router.tasks.create('main','Inspect output',verification_mode='manual')
        assert not router.call('task.review',{'conversation_id':'main','task_id':task['task_id']},remote=True)['ok']
        router.set_profile('main','read_only')
        assert not router.review_task('main',task['task_id'])['ok']
        router.set_profile('main','project')
        assert router.review_task('main',task['task_id'])['payload']['state']=='complete'
    finally:router.close();catalog.close()
