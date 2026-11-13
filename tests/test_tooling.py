import hashlib
import time
from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


def test_project_tools_edit_run_verify_and_isolate_jobs(tmp_path):
    root=tmp_path/'project';root.mkdir()
    catalog=Catalog(tmp_path/'state')
    project=catalog.create_project('test',root)
    first=catalog.create_conversation('one',project['id'])['id']
    second=catalog.create_conversation('two',project['id'])['id']
    router=ToolRouter(catalog,tmp_path/'state')
    def call(name,**params):
        value=router.call(name,{'conversation_id':first,**params})
        assert value['ok'],value
        return value['payload']
    try:
        call('file.write',path='hello.txt',text='before',create=True)
        read=call('file.read',path='hello.txt')
        assert 'sha256' not in read and 'data_base64' not in read
        call('file.edit',path='hello.txt',old='before',new='after')
        assert (root/'hello.txt').read_text()=='after'
        call('plan.update',goal='Verify file',plan='1. write done\n2. verify running',state='active')
        job=call('job.start',request_id='verify',command="if ((Get-Content -Raw hello.txt) -ne 'after') { exit 2 }; Write-Output 'verified'")
        assert not router.call('job.status',{'conversation_id':second,'job_id':job['job_id']})['ok']
        deadline=time.monotonic()+15
        result=job
        while result['state'] in ('accepted','running') and time.monotonic()<deadline:
            result=call('job.wait',job_id=job['job_id'],timeout_s=1)
        assert result['exit_code']==0 and 'verified' in result['output']
        router.stop_all()
        assert not router.call('file.write',{'conversation_id':first,'path':'other','text':'no','create':True})['ok']
        router.resume()
        assert 'Verify file' in call('bridge.context')
        assert not router.call('workspace.list',{'conversation_id':'main'})['ok']
    finally:
        router.close();catalog.close()
