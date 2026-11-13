"""Windows contract consumed by Muse's existing VM workflows."""
import platform
from .execution import effect_of,requires_project


CONTRACT_VERSION=2


def manifest(router,cid):
    from .shells import describe_shells
    conversation=router.catalog.conversation(cid)
    project=router.catalog.project(conversation['project_id']) if conversation['project_id'] else None
    policy=router.execution.policy(cid)
    tools={}
    for name,schema in (router.connection_specs or router.registration_specs()).items():
        if name=='bridge.health':continue
        effect=effect_of(name)
        try:router.execution.check(cid,name);enabled=True
        except ValueError:enabled=False
        if name == 'mcp.call':
            effect = 'grant_dependent'
            if project and policy['profile'] == 'read_only' and not policy['paused']:
                enabled = any('read' in server['grants'].values()
                              for server in router.mcp.servers(cid) if server['enabled'])
        if not project and requires_project(name):enabled=False
        if policy['paused'] and name in ('mcp.tools','mcp.read','mcp.call'):enabled=False
        if not project and name=='artifact.add':enabled=False
        if name in ('vision.observe','screen.look') and not router.vision.status(cid)['enabled']:enabled=False
        tools[name]={'effect':effect,'enabled':enabled,'required':schema['required'],'optional':schema['optional'],
                     'description':schema['description']}
    return {'contract_version':CONTRACT_VERSION,'runtime':'Windows native','platform':platform.platform(),
        'conversation_id':cid,'project':{'id':project['id'],'name':project['name'],'root':project['root']} if project else None,
        'scope':policy,'active_run_id':router.execution.active_run(cid),'sandbox':False,
        'paths':{'syntax':'Windows','tool_paths':'Relative to this project root; VM paths do not identify PC files',
                 'path_separator':'\\'},
        'execution':{'shell':'powershell','available_shells':describe_shells(),
                     'instructions':'Use shell.run for commands; select powershell, bash or cmd explicitly when needed. Use shell.job for returned running jobs and inspect exit_code.',
                     'inline_wait_cap_seconds':25,
                     'max_shell_job_wait_seconds':25,
                     'encoding_note':'PowerShell and UTF-8 child output preserve Unicode. cmd built-ins may replace non-ASCII characters; prefer PowerShell for Unicode text.',
                     'max_parallel_jobs':4,'max_job_seconds':3600,'max_job_wait_seconds':20,
                     'default_file_read_lines':400,'max_file_edit_bytes':4*1024*1024,
                     'file_editing':'Read numbered lines first; this chat remembers the file version. Use old/new or start_line/end_line, and read again if the file changed.',
                     'tool_result_max_encoded_bytes':24000,
                     'binding':'Omit run_id, scope_revision and request_id. Conversation is inferred only when one chat has an active run; otherwise pass conversation_id.',
                     'remote_admission':'Remote mutations require an active local request; stopped or finished requests cannot create a new run by retrying tools.'},
        'workflow':{'owner':'Muse VM','local_skill_runtime':False,
                    'instructions':'Use your existing skills/workflows. Adapt Windows execution through these tools; keep procedure reasoning on the VM.',
                    'project_guidance':'bridge.context exposes project instructions, AGENTS.md, goal and plan'},
        'results':{'accepted':'Operation admitted; inspect later outcome',
                   'succeeded':'Local tool returned successfully; goal verification is separate',
                   'remote_finished':'Muse reported its task terminal, including possible failure; not local verification',
                   'remote_reply_finished':'An attributable completed reply passed the SDK quiet/idle rules without a task terminal; not goal verification',
                   'unknown':'Outcome uncertain; do not replay mutating actions automatically'},
        'tools':tools}


def prompt_contract(router,cid):
    data=manifest(router,cid)
    # Do not repeat every schema on every user send; registered tools carry them.
    return {key:data[key] for key in ('contract_version','runtime','conversation_id','scope','active_run_id',
        'paths','execution','workflow','results')}
