"""Muse command contracts and project-bound dispatch; no local agent loop."""
import json
import platform
import threading
import copy
import hashlib
import uuid
from collections import OrderedDict
from .execution import Execution,effect_of,requires_project


def parameter(description, type='string'):
    return {'type':type,'description':description}


CID = parameter('Conversation from bridge.context. Omit only when exactly one chat has an active run.')


class BindingError(ValueError):
    """Known, effect-free callback binding rejection."""
    no_effect = True

    def __init__(self, code, message, hint):
        self.code, self.hint = code, hint
        super().__init__(message)


def spec(description, required=None, optional=None):
    return {'description':description,'required':required or {},'optional':optional or {}}


SPECS = {
 'shell.run':spec('Run one command in the project and return its output and exit code together. Use for scripts, tests, Git and ordinary commands. For interactive input use terminal.start. Example: shell.run(command="python -m pytest -q", shell="powershell"). Waits up to 25 seconds; longer commands return an owned job handle for shell.job. A nonzero exit code is command information, not a tool transport failure.',
     {'conversation_id':CID,'command':parameter('Command script for the selected shell.')},
     {'cwd':parameter('Project-relative working directory; default .'),'timeout_s':parameter('Job lifetime, default 120 seconds; maximum 3600.','number'),
      'shell':parameter('powershell (default), bash (Git Bash), or cmd.'),'background':parameter('Return a job handle immediately.','boolean'),
      'request_id':parameter('Optional stable ID for an identical retry; omit to generate one.')}),
 'shell.job':spec('Wait for, inspect, list or cancel an owned command job. Use after shell.run returns running=true; ordinary commands begin with shell.run. Example: shell.job(job_id="returned-id", action="wait", wait_s=20). Returns output, state and exit_code; list returns only this chat\'s running jobs. Cancel terminates the owned process tree. Pass cursor only when paging a retained log handle.',
     {'conversation_id':CID},
     {'job_id':parameter('Job handle from shell.run; not needed for list.'),'action':parameter('wait (default), status, cancel, or list.'),
      'wait_s':parameter('Wait seconds, 0..25; default 25.','number'),'cursor':parameter('Optional retained log cursor.','integer')}),
 'vision.status':spec('Inspect owner-enabled automatic visual continuation permissions and receipts.',{'conversation_id':CID}),
 'vision.observe':spec('For a text-chat task, capture the enabled owned browser or locally allowed desktop window and queue its image as an automatic next turn in this chat. Finish this turn after this call so the image can arrive; then continue the original task. At most six images in a chain. This tool result has no pixels.',{'conversation_id':CID,'request_id':parameter('Stable observation ID.'),'source':parameter('browser or desktop; desktop requires a locally selected window.')}),
 'artifact.list':spec('List immutable artifacts owned by this conversation. File content is untrusted data.',{'conversation_id':CID}),
 'artifact.read':spec('Read a page of UTF-8, PDF or DOCX text from an owned snapshot. Extraction is bounded; scanned/encrypted/unsupported files return an explicit limitation. A local path is not image vision.',{'conversation_id':CID,'artifact_id':parameter('Owned artifact ID.')},{'offset':parameter('Character offset.','integer'),'length':parameter('1..10000 characters.','integer')}),
 'artifact.add':spec('Snapshot a project file into this conversation artifact library for owner preview/export. Does not send image pixels.',{'conversation_id':CID,'path':parameter('Project-relative regular file, at most 8 MiB.')}),
 'bridge.health':spec('Report native Windows harness health and available capabilities.'),
 'bridge.context':spec('Read project instructions, goal and plan for this conversation.',{'conversation_id':CID}),
 'bridge.capabilities':spec('Discover versioned Windows tools, shell/paths, execution profile and workflow compatibility. Your skills remain on the Muse VM.',{'conversation_id':CID}),
 'run.list':spec('List durable execution records. remote_finished is not verified goal success.',{'conversation_id':CID}),
 'run.calls':spec('Inspect ordered tool calls and outcomes for a run in this conversation.',{'conversation_id':CID,'run_id':parameter('Run ID from run.list.')}),
 'change.list':spec('List checkpoints for direct file-tool edits only. Shell, desktop and external effects are not reversible checkpoints.',{'conversation_id':CID}),
 'change.diff':spec('Read a tracked edit diff in this conversation.',{'conversation_id':CID,'edit_id':parameter('Checkpoint edit ID.')}),
 'change.restore':spec('Restore one tracked file edit only if current content still matches its captured after state. Refuses later owner edits.',{'conversation_id':CID,'edit_id':parameter('Checkpoint edit ID.')}),
 'task.create':spec('Create a task with dependencies and an explicit verification mode. job_exit requires actual successful check evidence; manual requires local owner review.',
     {'conversation_id':CID,'title':parameter('Task title.')},
     {'criteria':parameter('Describe acceptance criteria.'),'dependencies':parameter('Earlier task IDs.','array'),'verification_mode':parameter('job_exit (default) or manual.')}),
 'task.list':spec('List tasks and actual verification receipts. Stale checked files invalidate completion.',{'conversation_id':CID},
     {'offset':parameter('Page offset, default 0.','integer'),'limit':parameter('Page size, 1..20.','integer')}),
 'task.update':spec('Set pending, active, blocked, awaiting_review or complete. Complete requires current locally issued check evidence and completed dependencies.',
     {'conversation_id':CID,'task_id':parameter('Task ID.'),'state':parameter('Requested task state.')},{'details':parameter('Progress or blocking details.')}),
 'task.check_files':spec('Observe project file existence/hashes. Observations alone do not verify semantic criteria or allow completion.',
     {'conversation_id':CID,'task_id':parameter('Task ID.'),'checks':parameter('List of objects with path and optional exists/sha256.','array')}),
 'task.verify_job':spec('Read an owned check job exit code and issue a local receipt. File-bound checks must be prepared in job.start using verification_task_id and verification_paths.',
     {'conversation_id':CID,'task_id':parameter('Task ID.'),'job_id':parameter('Completed owned check job.')},
     {'paths':parameter('Exact project-relative check inputs bound before job start; empty means exit-status only.','array')}),
 'project.note':spec('Record a source-linked project decision/constraint/progress fact; this does not install skills or grant permissions.',
     {'conversation_id':CID,'kind':parameter('decision, constraint or progress'),'text':parameter('Fact/decision text, at most 4000 characters.')},
     {'source':parameter('Source reference or owner statement; not an executable instruction.'),'supersedes':parameter('Current same-kind note ID being corrected.')}),
 'project.notes':spec('Read project-scoped source-linked notes, including correction history.',{'conversation_id':CID},{'limit':parameter('1..20, default 10.','integer')}),
 'project.handoff':spec('Build bounded, source-linked project status for a new chat. This is a summary, not a native hidden-state fork.',{'conversation_id':CID}),
 'conversation.handoff':spec('Create a separate paused conversation with a summary seed. Owner selects it; no hidden-state fork or automatic work.',{'conversation_id':CID},{'title':parameter('Optional new chat title.')}),
 'plan.update':spec('Persist a Markdown plan or goal state. Completion must follow verification.',{'conversation_id':CID},
                    {'plan':parameter('Markdown plan with task statuses.'),'goal':parameter('Goal description.'),
                     'state':parameter('idle, active, paused, complete or blocked.')}),
 'workspace.list':spec('List a directory in the conversation project.',{'conversation_id':CID}, {'path':parameter('Relative path; default .')}),
 'workspace.search':spec('Find literal text in project files, with bounded results.',{'conversation_id':CID,'query':parameter('Literal search text.')}, {'path':parameter('Relative directory; default .')}),
 'file.read':spec('Read numbered lines from a project file and remember its current version for this chat. Use before file.edit; for finding text across files use file.search. Example: file.read(path="src/app.py", start_line=1, max_lines=400). Returns text, encoding, total_lines and a more pointer. Binary files return their kind and a suitable inspection tool. Long lines are visibly shortened.',{'conversation_id':CID,'path':parameter('Relative file path.')},
                   {'start_line':parameter('First line, starting at 1.','integer'),'max_lines':parameter('Lines requested; default 400.','integer')}),
 'file.write':spec('Create a text file and its parent directories, or replace a file after reading its current content. Use file.edit for smaller changes. Example: file.write(path="src/new.py", text="print(1)\\n"). Existing files require a current read or overwrite=true. Returns the path, size and checkpoint; encoding and line endings are preserved for existing files. New Windows scripts use CRLF.',
                   {'conversation_id':CID,'path':parameter('Relative file path.'),'text':parameter('UTF-8 file contents.')},
                   {'overwrite':parameter('Explicitly allow replacement without a current read.','boolean')}),
 'file.edit':spec('Edit a file already read in this chat. Use old/new for a unique text match, or an inclusive line range when precise lines are easier. Example: file.edit(path="src/app.py", old="return 1", new="return 2"). Accepts ordered edits=[...] atomically and replace_all. Preserves encoding/BOM/newlines, refuses intervening changes, and returns numbered changed regions. Line numbers change after every edit.',
                  {'conversation_id':CID,'path':parameter('Relative file path.')},
                  {'old':parameter('Unique text to replace.'),'new':parameter('Replacement text.'),
                   'replace_all':parameter('Replace every match.','boolean'),'edits':parameter('Ordered old/new or line-range edit objects.','array'),
                   'start_line':parameter('First inclusive line.','integer'),'end_line':parameter('Last inclusive line; start_line-1 inserts.','integer')}),
 'file.list':spec('List an indented project tree, directories first, honoring .gitignore. Use to discover files; use file.search to locate text. Example: file.list(path="src", depth=2). Returns up to 200 entries with counts and truncation details. Private application directories and links outside the project remain excluded.',
     {'conversation_id':CID},{'path':parameter('Relative directory; default .'),'depth':parameter('Directory levels; default 2.','integer')}),
 'file.search':spec('Search project text with regex, or use literal=true for exact text. Honors .gitignore and bounds results. Example: file.search(pattern="TODO", glob="*.py", context=1). Choose mode=files for paths or mode=count for matching-line counts. Returns grouped numbered matches and a narrowing hint when truncated; use file.read to inspect a whole region.',
     {'conversation_id':CID,'pattern':parameter('Regex or literal search text.')},
     {'path':parameter('Relative file/directory; default .'),'glob':parameter('Optional file glob.'),'mode':parameter('content (default), files, or count.'),
      'context':parameter('Context lines, 0..10.','integer'),'literal':parameter('Treat pattern as exact text.','boolean'),
      'ignore_case':parameter('Case-insensitive search.','boolean'),'max_results':parameter('1..50 results; default 50.','integer')}),
 'git.status':spec('Read git status in the selected project.',{'conversation_id':CID}),
 'git.diff':spec('Read a bounded page of Git diff without external diff/textconv drivers.',{'conversation_id':CID},
     {'path':parameter('Optional project-relative file.'),'staged':parameter('Inspect staged changes.','boolean'),
      'offset':parameter('Character offset.','integer'),'length':parameter('Page characters, 1..10000.','integer')}),
 'git.branches':spec('List local branches in the selected project.',{'conversation_id':CID}),
 'git.worktrees':spec('Inspect Git worktrees and managed creation records.',{'conversation_id':CID}),
 'git.worktree.create':spec('Create a fresh isolated branch/worktree and separate project/chat. Never switches or cleans the original checkout; owner selects the new chat.',
     {'conversation_id':CID,'branch':parameter('New branch name.')},{'base':parameter('Optional existing local base ref; default HEAD.')}),
 'hook.status':spec('Read locally configured hook invocations/jobs; remote tools cannot enable executable hooks.',{'conversation_id':CID}),
 'mcp.servers':spec('List locally configured stdio MCP servers for this project. Remote tools cannot configure or enable servers.',{'conversation_id':CID}),
 'mcp.tools':spec('Discover actual MCP schemas and explicit local grants for an enabled server.',{'conversation_id':CID,'server_id':parameter('Server ID from mcp.servers.')}),
 'mcp.read':spec('Call only an MCP tool explicitly granted read access. Tool annotations do not grant permission.',
     {'conversation_id':CID,'server_id':parameter('Server ID.'),'tool_name':parameter('Exact MCP tool name.'),'arguments':parameter('Arguments matching the discovered JSON schema.','object')}),
 'mcp.call':spec('Call an explicitly granted MCP tool. Reuse request_id only for the identical operation; unknown calls are not replayed.',
     {'conversation_id':CID,'server_id':parameter('Server ID.'),'tool_name':parameter('Exact MCP tool name.'),'arguments':parameter('Arguments matching the discovered JSON schema.','object'),
      'request_id':parameter('Stable unique operation ID.') }),
 'mcp.close':spec('Close this locally owned MCP server connection.',{'conversation_id':CID,'server_id':parameter('Server ID.')}),
 'terminal.start':spec('Start an owned ConPTY program in this project. Arguments are a JSON list with an absolute executable. Returns promptly; use terminal.read. Never attaches to another user terminal.',
     {'conversation_id':CID,'argv':parameter('Executable and arguments.','array'),'request_id':parameter('Stable terminal creation request ID.')},
     {'cwd':parameter('Relative project directory; default .'),'rows':parameter('5..200; default 24.','integer'),'cols':parameter('20..300; default 80.','integer')}),
 'terminal.list':spec('List owned interactive terminal sessions for this conversation.',{'conversation_id':CID}),
 'terminal.read':spec('Read bounded terminal output and input acknowledgment states.',{'conversation_id':CID,'terminal_id':parameter('Terminal ID.')},{'cursor':parameter('Output cursor.','integer')}),
 'terminal.write':spec('Write literal input to this conversation’s terminal once per request_id. Include carriage return to submit a line. Unknown input is not replayed.',
     {'conversation_id':CID,'terminal_id':parameter('Terminal ID.'),'text':parameter('Literal input, up to 10000 bytes.'),'request_id':parameter('Stable input operation ID.')}),
 'terminal.resize':spec('Resize the owned terminal viewport.',{'conversation_id':CID,'terminal_id':parameter('Terminal ID.'),'rows':parameter('5..200.','integer'),'cols':parameter('20..300.','integer')}),
 'terminal.stop':spec('Stop the owned terminal and its process descendants.',{'conversation_id':CID,'terminal_id':parameter('Terminal ID.')}),
 'job.start':spec('Start a Windows PowerShell job. Runs as the PC user, with their permissions. '
                  'Return promptly, then use job.wait/status to check output and exit code. Reuse request_id only for the same operation.',
                  {'conversation_id':CID,'request_id':parameter('Unique stable ID for this operation.'),
                   'command':parameter('Windows PowerShell command, not bash.')},
                  {'cwd':parameter('Relative project working directory; default .'),'timeout_s':parameter('Time limit seconds, max 3600.','integer'),
                   'verification_task_id':parameter('Task ID whose check inputs should be captured before launching this job.'),
                   'verification_paths':parameter('Project-relative stable check inputs; bound to the prepared task.','array')}),
 'job.status':spec('Read a job state and bounded output from a cursor.',{'conversation_id':CID,'job_id':parameter('Job ID.')}, {'cursor':parameter('Output cursor.','integer')}),
 'job.wait':spec('Wait briefly for output/state; repeat while running. Never assume success without exit_code.',
                 {'conversation_id':CID,'job_id':parameter('Job ID.')},
                 {'cursor':parameter('Output cursor.','integer'),'timeout_s':parameter('Wait up to 20 seconds.','integer')}),
 'job.cancel':spec('Cancel a job and its Windows process tree.',{'conversation_id':CID,'job_id':parameter('Job ID.')}),
}


class ToolRouter:
    def __init__(self, catalog, directory, activity=lambda _:None):
        from .jobs import JobManager
        from .browser import BrowserTools
        from .desktop import DesktopTools
        self.catalog, self.activity = catalog, activity
        self.execution=Execution(catalog)
        from .checkpoints import CheckpointStore
        self.checkpoints=CheckpointStore(__import__('pathlib').Path(directory)/'checkpoints')
        catalog.checkpoints=self.checkpoints
        from .tasks import TaskStore
        self.tasks=TaskStore(catalog);catalog.tasks=self.tasks
        from .artifacts import Artifacts
        self.artifacts=Artifacts(catalog)
        from .vision import VisionService
        self.vision=VisionService(catalog,self.execution,self.artifacts)
        from .continuity import ContinuityStore
        self.continuity=ContinuityStore(catalog);catalog.continuity=self.continuity
        self.jobs = JobManager(directory,activity=activity)
        from .shell_tools import ShellTools
        self.shell = ShellTools(self)
        from .git_tools import GitTools
        self.git=GitTools(catalog,__import__('pathlib').Path(directory).parent/'MuseBridge-Workspaces')
        from .hooks import HookService
        self.hooks=HookService(catalog,self.execution,self.jobs)
        self.execution.on_remote_finished=self.run_ended_hooks
        from .mcp_client import MCPBridge
        self.mcp=MCPBridge(catalog,self.execution,directory)
        from .terminals import TerminalManager
        self.terminals=TerminalManager(catalog,self.jobs,directory)
        self.browser = BrowserTools(__import__('pathlib').Path(directory)/'artifacts'/'browser')
        self.desktop = DesktopTools(__import__('pathlib').Path(directory)/'artifacts'/'desktop')
        self.desktop.execution_lock=self.execution.lock
        self.desktop.ocr_enabled=catalog.preference('windows_ocr',default=True)
        self.lock = threading.RLock()
        self.paused = False
        self.workspaces = {}
        self.failure_streaks = OrderedDict()
        self.progress_streaks = OrderedDict()
        self.connection_specs = None
        from .pack_tools import PackTools
        self.packs = PackTools(self)
        from .file_tools import FileTools
        self.files = FileTools(self)
        from .context_snapshot import ContextSnapshot
        self.context_snapshot = ContextSnapshot(self)
        from .edit_checks import EditChecks
        self.edit_checks = EditChecks(self)
        from .finish_line import FinishLine
        self.finish_line = FinishLine(self)
        with catalog.lock, catalog.db:
            catalog.db.execute('CREATE TABLE IF NOT EXISTS job_links(job_id TEXT PRIMARY KEY,conversation_id TEXT)')
        from .shadow_git import ShadowGit
        self.shadow = ShadowGit(self,directory)
        self.execution.on_run_started=self.shadow.begin
        self.execution.on_run_finished=self.shadow.finish

    def specs(self):
        from .tool_surface import V2_SPECS
        schemas=copy.deepcopy({**SPECS,**self.browser.specs(),**self.desktop.specs(),**V2_SPECS})
        for name,schema in schemas.items():
            if name=='bridge.health':continue
            schema['optional'].setdefault('scope_revision',parameter('Current revision from bridge.capabilities; stale values are refused.','integer'))
            if name!='run.calls':schema['optional'].setdefault('run_id',parameter('Active run ID from context; omit for standalone inspection.'))
        return schemas

    def registration_specs(self):
        from .tool_surface import registration_specs
        schemas=registration_specs(self)
        from .shells import resolve_shell
        try:
            resolve_shell('bash')
        except RuntimeError:
            schemas['shell.run']['optional']['shell']['description'] = 'powershell (default) or cmd.'
        return schemas

    def begin_registration(self):
        self.connection_specs = self.registration_specs()
        return copy.deepcopy(self.connection_specs)

    def _policy_name(self, name, cid, params):
        if name == 'mcp.call' and self.mcp.grant(cid,params.get('server_id'),params.get('tool_name')) == 'read':
            return 'mcp.read'
        return name

    def _bind(self, name, params, expected_conversation_id, remote):
        """Resolve one immutable callback scope, without trusting model bookkeeping."""
        params = dict(params)
        cid = params.get('conversation_id')
        if cid is None:
            candidates = self.catalog.rows("""SELECT DISTINCT c.id,c.title FROM runs r
                JOIN conversations c ON c.id=r.conversation_id
                WHERE r.state IN ('sending','acknowledged') ORDER BY c.created""")
            if len(candidates) != 1:
                active = ', '.join(row['title'][:120] + ' (' + row['id'] + ')' for row in candidates[:20]) or 'none'
                raise BindingError('ambiguous_conversation', 'A unique active conversation could not be inferred.',
                    'Pass conversation_id. Active: ' + active + '.')
            cid = candidates[0]['id']
        if not isinstance(cid, str) or not cid:
            raise BindingError('bad_arguments', 'Invalid conversation_id.', 'Pass the conversation_id from bridge.context.')
        if expected_conversation_id is not None and cid != expected_conversation_id:
            raise BindingError('conversation_changed', 'Conversation changed; request does not match the active chat.',
                'Refresh bridge.context in the selected chat before calling this tool.')
        conversation = self.catalog.conversation(cid)
        if requires_project(name) and not conversation['project_id']:
            raise BindingError('no_project','This conversation has no PC workspace.',
                'Choose a workspace using the folder control in MuseBridge. Keep this chat; attach an existing workspace or choose a folder, then send a new request.')
        supplied = params.get('run_id')
        if supplied is not None and (not isinstance(supplied, str) or not supplied):
            raise BindingError('bad_arguments', 'Invalid legacy run_id.', 'Omit run_id; the harness binds the current run.')
        params['conversation_id'] = cid
        if name == 'desktop.act' and 'window' not in params and 'window_handle' in params:
            params['window'] = params['window_handle']
        if name == 'run.calls' and 'history_run' in params:
            if supplied is not None and supplied != params['history_run']:
                raise BindingError('bad_arguments', 'Conflicting historical run IDs.', 'Pass history_run only.')
            params['run_id'] = params['history_run']
        # Validate policy before the active-run gate so Stop/Read only retain
        # their specific guidance even when Stop retired the previous run.
        policy_name = self._policy_name(name,cid,params)
        self.execution.check(cid, policy_name, params.get('scope_revision'))
        if remote and effect_of(policy_name) not in ('read', 'cleanup') and not supplied and not self.execution.active_run(cid):
            raise BindingError('no_active_run', 'No active local request owns this remote mutation.',
                'Start a new request in MuseBridge. Do not resume a stopped or finished request by retrying its tools.')
        return cid, params

    def _respond(self, name, params, result, record=None, cid=None, response_adapter=None):
        """Keep detailed journals local and measure only the actual wire response."""
        from .tool_results import shape_result
        if result.get('ok') and isinstance(result.get('payload'),dict):
            payload = result['payload']
            message = None
            if name in ('file.search','workspace.search') and payload.get('count') == 0:
                message = ('No matches in the scanned portion; narrow the capped search.'
                           if payload.get('truncated') else 'No matches.')
            if name == 'file.read' and payload.get('text') == '':
                message = 'File is empty.' if payload.get('total_lines') == 0 else 'No lines at this offset; choose an earlier start_line.'
            if message:
                result = {**result, 'payload': {**payload, 'message': message}}
        wire = shape_result(result, name)
        run_id = record[1] if record else (self.execution.active_run(cid) if cid else None)
        if run_id:
            progress = self.catalog.rows('SELECT r.revision FROM finish_requests_v1 r '
                'JOIN finish_runs_v1 f ON f.request_id=r.id WHERE f.run_id=?',(run_id,))
            revision = progress[0]['revision'] if progress else 0
            payload = result.get('payload')
            command_ok = (result.get('ok') and isinstance(payload,dict) and
                (name in ('shell.run','shell.job','job.wait','job.status') and
                 payload.get('exit_code') == 0 and payload.get('state') == 'succeeded'
                 or name in ('task.verify','task.verify_job') and
                 payload.get('verification',payload).get('passed') is True))
            with self.lock:
                count, previous_revision, warned = self.progress_streaks.get(run_id,(0,0,False))
                made_progress = revision != previous_revision or command_ok or name == 'plan.update' and result.get('ok')
                if not made_progress and count >= 15 and not warned:
                    hint = '15 calls without progress. Consider updating the plan or telling the user what is blocking.'
                    if wire['ok']:
                        wire['hint'] = hint
                    else:
                        wire['error']['hint'] += ' ' + hint
                    warned = True
                self.progress_streaks[run_id] = (0 if made_progress else count+1,revision,warned)
                self.progress_streaks.move_to_end(run_id)
                while len(self.progress_streaks)>256:
                    self.progress_streaks.popitem(last=False)
            arguments = ({key: value for key, value in params.items()
                         if key not in ('run_id', 'scope_revision', 'request_id')}
                         if isinstance(params,dict) else params)
            signature = hashlib.sha256(json.dumps([name, arguments], sort_keys=True,
                ensure_ascii=True, default=str).encode()).digest()
            with self.lock:
                if wire['ok']:
                    self.failure_streaks.pop(run_id, None)
                else:
                    previous, count = self.failure_streaks.get(run_id, (None, 0))
                    count = count + 1 if previous == signature else 1
                    self.failure_streaks[run_id] = (signature, count)
                    self.failure_streaks.move_to_end(run_id)
                    while len(self.failure_streaks) > 256:
                        self.failure_streaks.popitem(last=False)
                    if count >= 3:
                        suggestion = ('Read the file again and use a narrower range or a different match.' if name.startswith('file.')
                            else 'Inspect bridge.context and choose a different tool or arguments.')
                        wire['error']['hint'] += f' This exact call has failed {count} times. Change the approach: ' + suggestion
                        wire = shape_result(wire, name)
        wire = shape_result(wire, name)
        if response_adapter is not None:
            wire = response_adapter(wire)
        if record:
            self.record_statistics(record[0], wire, params)
        return wire

    @property
    def paused(self):return self.execution.paused

    @paused.setter
    def paused(self,value):
        self.execution.pause(value)
        if value and hasattr(self,'terminals'):self.terminals.revoke_inputs()
        if value and hasattr(self,'vision'):self.vision.hold(cancel=True)

    def set_profile(self,cid,profile):
        before=self.execution.policy(cid);result=self.execution.set_profile(cid,profile)
        if before['revision']!=result['revision']:
            scope=self.execution.scope(cid)
            cids=[c['id'] for c in self.catalog.conversations(True) if self.execution.scope(c['id'])==scope]
            self.terminals.revoke_inputs(cids)
            if profile == 'desktop' and self.connection_specs is not None and 'desktop.act' not in self.connection_specs:
                self.activity('Desktop control enabled. Reconnect to register its tool pack.')
        return result

    def call(self,name,params,timeout_ms=None,*,expected_conversation_id=None,remote=False,admission_generation=None,response_adapter=None):
        record=None;dispatched=False;started=False;cid=None;effect='read'
        if not isinstance(name,str):
            return self._respond('unknown.tool', params, {'ok':False,'error':{
                'code':'bad_arguments','message':'Tool name must be a string.'}},response_adapter=response_adapter)
        try:
            if name not in self.specs():raise ValueError('Unknown command')
            if not isinstance(params,dict):raise ValueError('Parameters must be an object')
            if name == 'bridge.health':
                cid = expected_conversation_id or params.get('conversation_id') or 'main'
                params = {**params,'conversation_id':cid}
                record = self.execution.start_call(cid, name, 'read')
                result = self._dispatch(name, params, timeout_ms)
                dispatched = True
                self.execution.finish_call(cid, *record[:2], name, 'succeeded', own_run=record[2])
                return self._respond(name, params, result, record, cid,response_adapter)
            cid,params=self._bind(name,params,expected_conversation_id,remote)
            # run.calls addresses a historical run, rather than executing inside it.
            run_id=None if name in ('run.calls','change.list') else params.get('run_id')
            policy_name = self._policy_name(name,cid,params)
            with self.execution.admission(cid,policy_name,params.get('scope_revision')) as (effect,guard):
                if admission_generation is not None and admission_generation!=self.execution.generation:raise ValueError('Queued local action revoked by Stop Work')
                record=self.execution.start_call(cid,name,effect,run_id)
                call_id,run_id,own=record
                if own and effect=='project_write' and name not in ('change.restore',):
                    self.shadow.begin(cid,run_id)
                # Status, discovery and cancellation remain available even if a
                # configured hook is broken. Hook jobs never recurse via Router.
                hooked=not name.startswith(('bridge.','run.','hook.','job.status','job.wait','job.cancel')) and effect!='cleanup'
                if hooked:
                    for key in self.specs()[name]['required']:
                        if key == 'request_id':continue
                        if key not in params:raise ValueError('Missing parameter: '+key)
                    self.hooks.before_tool(cid,name,run_id,params,guard)
                # Hook retry deduplication hashes the caller's semantic inputs,
                # not a freshly generated effect ID. Effects still get unique
                # operation IDs before the adapter can admit them.
                if 'request_id' in self.specs()[name]['required'] and 'request_id' not in params:
                    params['request_id'] = str(uuid.uuid4())
                def dispatch():
                    nonlocal started
                    guard()
                    self.execution.call_started(cid,call_id,run_id,name)
                    started=True
                    return self._dispatch(name,params,timeout_ms,execution_run=run_id,execution_guard=guard,execution_effect=effect)
                if name in ('mcp.read','mcp.call','git.worktree.create','vision.observe','screen.look','shell.run','shell.job','task.verify','terminal.open','terminal.send','desktop.launch','desktop.act') or name == 'change.restore' and 'edit_id' not in params:
                    result=dispatch()
                elif name.startswith('browser.'):
                    # Browser effects can wait in another thread's queue: recheck at execution.
                    def browser_guard():
                        nonlocal started
                        guard();self.execution.call_started(cid,call_id,run_id,name)
                        started=True
                    result=self._dispatch(name,params,timeout_ms,browser_guard=browser_guard,execution_run=run_id,execution_guard=guard,execution_effect=effect)
                elif name in ('file.write','file.edit'):
                    # FileTools holds the gate through atomic publication only;
                    # syntax feedback and project checks do not block Stop Work.
                    result=dispatch()
                elif effect in ('project_write','desktop_action'):
                    with self.execution.lock:result=dispatch()
                else:result=dispatch()
                dispatched=True
                state='succeeded' if result['ok'] else 'failed'
                from .tool_results import normalize_error
                self.execution.finish_call(cid,call_id,run_id,name,state,
                    None if result['ok'] else normalize_error(result.get('error',{}),name)['code'],own)
                if hooked:
                    try:
                        partial = isinstance(result.get('payload'),dict) and result['payload'].get('partial')
                        hook_state = 'partial' if partial else state
                        if partial:self.catalog.event(cid,'tool.partial',{'call_id':call_id,'tool':name})
                        result['lifecycle']=self.hooks.run_event(cid,'after_tool',call_id,tool=name,run_id=run_id,state=hook_state)
                    except Exception as exc:self.catalog.event(cid,'hook.dispatch_failed',{'call_id':call_id,'error_code':type(exc).__name__})
                return self._respond(name, params, result, record, cid,response_adapter)
        except Exception as exc:
            original_record = record
            rejected_cid = expected_conversation_id or cid
            if rejected_cid is None and isinstance(params,dict):
                rejected_cid = params.get('conversation_id')
            if record is None and isinstance(rejected_cid,str):
                try:
                    cid = rejected_cid
                    logged_name = name if name in self.specs() else 'unknown.tool'
                    record = self.execution.start_call(cid, logged_name, 'rejected')
                except (ValueError, RuntimeError):
                    record = None
            state='unknown' if started and effect not in ('read','cleanup') and not getattr(exc,'no_effect',False) else 'failed'
            error={'message':str(exc)[:1000],'code':getattr(exc,'code',type(exc).__name__)}
            if getattr(exc,'hint',None):
                error['hint']=exc.hint[:2000]
            if original_record:
                error['outcome']=state
                if state=='unknown':error['message']+=' Outcome unknown; inspect the result before any retry.'
            from .tool_results import normalize_error
            if record and not dispatched:
                self.execution.finish_call(cid,*record[:2],name,state,normalize_error(error,name)['code'],record[2])
                if started:
                    try:self.hooks.run_event(cid,'after_tool',record[0],tool=name,run_id=record[1],state=state)
                    except Exception:pass
            result = {'ok': False, 'error': error}
            return self._respond(name, params, result, record, cid,response_adapter)

    def record_statistics(self, call_id, result, params):
        """Record measurable result size without copying parameters or output."""
        from .metrics import result_size, error_summary
        import sqlite3
        try:
            self.execution.record_statistics(call_id, result_size(result), error_summary(result, params))
        except (sqlite3.Error, TypeError, ValueError):
            # Statistics must not turn an already completed effect into a failed
            # tool response. NULL metrics remain visible to the stats reader.
            self.statistics_failures = getattr(self, 'statistics_failures', 0) + 1

    def _dispatch(self,name,params,timeout_ms=None,*,browser_guard=None,execution_run=None,execution_guard=None,execution_effect=None):
        if name not in self.specs():
            return {'ok':False,'error':{'message':'Unknown command'}}
        try:
            for key in self.specs()[name]['required']:
                if key not in params:
                    raise ValueError('Missing parameter: '+key)
            if name == 'bridge.health':
                cid = params.get('conversation_id','main')
                conversation = self.catalog.conversation(cid)
                from .capabilities import manifest
                capabilities = manifest(self,cid)
                return {'ok':True,'payload':{'runtime':'Windows native','platform':platform.platform(),
                    'tools':list(self.connection_specs or self.registration_specs()),
                    'enabled_tools':[tool for tool,item in capabilities['tools'].items() if item['enabled']],
                    'project_bound':bool(conversation['project_id']),'profile':capabilities['scope']['profile'],
                    'paused':self.paused,'shell':'powershell','sandbox':False,'contract_version':2,'skills_owner':'Muse VM',
                    'note':'Health describes connection and admission. It does not verify that a command executed.'}}
            cid = params['conversation_id']
            context = self.catalog.context(cid)
            self.activity('Tool: '+name)
            from .pack_tools import NAMES
            if name in NAMES:
                result = self.packs.call(name,cid,params,execution_run,browser_guard or execution_guard)
            elif name in ('task.set','task.verify','change.review') or name == 'change.restore' and 'edit_id' not in params:
                from .core_tools_v2 import CoreToolsV2
                result = CoreToolsV2(self).call(name,cid,params,execution_run,execution_guard)
                if name == 'task.set' and result.get('failed') and not result.get('succeeded'):
                    return {'ok':False,'error':{'code':'bad_arguments',
                        'message':'All task entries were rejected. Inspect the per-item errors and current tasks before retrying.',
                        'hint':'Correct the rejected entries in task.set; preserve any existing task IDs.',
                        'items':result['items'],'tasks':result['tasks']}}
            elif name == 'bridge.context':
                result = self.context_snapshot.render(cid,params.get('section'))
            elif name == 'bridge.capabilities':
                from .capabilities import manifest
                result=manifest(self,cid)
                result['tools'] = {tool: {'effect': item['effect'], 'enabled': item['enabled']}
                                   for tool, item in result['tools'].items()}
                result['schema_hint'] = 'Full argument schemas are registered on this device; this summary lists availability and effects.'
            elif name == 'shell.run':
                from .shells import resolve_shell, describe_shells
                try:
                    resolve_shell(params.get('shell', 'powershell'))
                except RuntimeError:
                    available = [key for key, value in describe_shells().items() if value['available']]
                    return {'ok':False,'error':{'code':'shell_unavailable',
                        'message':'The requested shell is unavailable.',
                        'hint':'Available shells: '+', '.join(available)}}
                result=self.shell.run(cid,params,execution_guard,execution_run)
            elif name == 'shell.job':result=self.shell.job(cid,params)
            elif name in ('file.read','file.write','file.edit','file.list','file.search'):
                result=self.files.call(name,cid,params,execution_run,execution_guard=execution_guard)
            elif name == 'run.list':result=self.execution.runs(cid)
            elif name == 'run.calls':result=self.execution.calls(cid,params['run_id'])
            elif name=='artifact.list':result=self.artifacts.list(cid)
            elif name=='artifact.read':result=(self.artifacts.read(cid,params['artifact_id'],params.get('offset',0),params.get('length',10000))
                if params.get('artifact_id') else self.artifacts.list(cid))
            elif name=='artifact.add':
                if not context['project']:raise ValueError('Choose a project before registering project artifacts')
                from .workspace import Workspace
                result=self.artifacts.import_file(cid,Workspace(context['project']['root']).resolve(params['path']),source='Project file: '+params['path'])
            elif name=='vision.status':result=self.vision.status(cid)
            elif name=='vision.observe':result=self.vision.capture(self,cid,execution_run,params['request_id'],params['source'],execution_guard)
            elif name == 'change.list':result=self.checkpoints.list(cid,params.get('run_id'))
            elif name == 'change.diff':result=self.checkpoints.diff(cid,params['edit_id'])
            elif name == 'change.restore':
                if not context['project']:raise ValueError('Choose a project before restoring changes')
                result=self.checkpoints.restore(cid,params['edit_id'],expected_root=context['project']['root'])
                if not result.get('already_restored'):
                    self.finish_line.changed(cid,execution_run,result['path'])
            elif name=='task.create':
                result=self.tasks.create(cid,params['title'],params.get('criteria',''),params.get('dependencies',[]),params.get('verification_mode','job_exit'))
            elif name=='project.note':result=self.continuity.note(cid,params['kind'],params['text'],params.get('source',''),params.get('supersedes'))
            elif name=='project.notes':
                limit=params.get('limit',10)
                if type(limit) is not int or not 1<=limit<=20:raise ValueError('Note limit must be 1..20')
                result=self.continuity.list(cid,limit)
            elif name=='project.handoff':result=self.continuity.handoff(cid)
            elif name=='conversation.handoff':result=self.continuity.create_handoff(cid,params.get('title'))
            elif name=='hook.status':result=self.hooks.refresh(cid)
            elif name=='terminal.start':result=self.terminals.start(cid,params['argv'],params.get('cwd','.'),params['request_id'],params.get('rows',24),params.get('cols',80))
            elif name=='terminal.list':result=self.terminals.list(cid)
            elif name=='terminal.read':result=self.terminals.status(cid,params['terminal_id'],params.get('cursor',0))
            elif name=='terminal.write':result=self.terminals.write(cid,params['terminal_id'],params['text'],params['request_id'])
            elif name=='terminal.resize':result=self.terminals.resize(cid,params['terminal_id'],params['rows'],params['cols'])
            elif name=='terminal.stop':result=self.terminals.stop(cid,params['terminal_id'])
            elif name=='mcp.servers':result=self.mcp.servers(cid)
            elif name=='mcp.tools':result=self.mcp.tools(cid,params['server_id'])
            elif name in ('mcp.read','mcp.call'):
                result=self.mcp.call(cid,params['server_id'],params['tool_name'],params['arguments'],
                    read_only=name=='mcp.read' or execution_effect=='read',guard=execution_guard,request_id=params.get('request_id',uuid.uuid4().hex))
                if not result['ok']:return {'ok':False,'payload':result,'error':{'message':'MCP tool reported an error; inspect outcome before retrying'}}
            elif name=='mcp.close':
                self.mcp._config(cid,params['server_id']);self.mcp.stop_server(params['server_id']);result={'closed':True}
            elif name=='git.branches':result=self.git.branches(cid)
            elif name=='git.worktrees':result=self.git.worktrees(cid)
            elif name=='git.worktree.create':result=self.git.create_worktree(cid,params['branch'],params.get('base'),guard=execution_guard)
            elif name=='git.diff':
                offset=params.get('offset',0);length=params.get('length',10000)
                if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=10000:raise ValueError('Invalid diff page')
                result=self.git.diff(cid,params.get('path'),params.get('staged',False))
                text=result['diff'];result['diff']=text[offset:offset+length];result['next_offset']=offset+length if len(text)>offset+length else None
            elif name=='task.list':
                offset=params.get('offset',0);limit=params.get('limit',20)
                if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=20:raise ValueError('Invalid task page')
                tasks=self.tasks.list(cid);result={'tasks':tasks[offset:offset+limit],'next_offset':offset+limit if len(tasks)>offset+limit else None,'total':len(tasks)}
            elif name=='task.update':result=self.tasks.update(cid,params['task_id'],params['state'],params.get('details'))
            elif name in ('task.check_files','task.verify_job'):
                if not context['project']:raise ValueError('Choose a project for task verification')
                root=context['project']['root']
                if name=='task.check_files':result=self.tasks.check_files(cid,params['task_id'],root,params['checks'])
                else:
                    hook_jobs=self.hooks.run_event(cid,'before_verification',params['task_id']+':'+params['job_id'])
                    if any(item.get('state')=='outcome_unknown' for item in hook_jobs['skipped']):raise OSError('A before-verification hook admission is uncertain')
                    statuses=[self.jobs.status(item['job_id']) for item in hook_jobs['jobs'] if item.get('job_id')]
                    if any(item['state'] in ('accepted','running') for item in statuses):
                        return {'ok':True,'payload':{'ready':False,'hook_jobs':hook_jobs['jobs'],'instruction':'Wait for these hook jobs, then request task.verify_job again; no verification receipt was issued yet.'}}
                    if any(item['state']!='succeeded' or item['exit_code']!=0 for item in statuses):raise ValueError('A before-verification hook did not succeed; task verification was not issued')
                    result=self.tasks.verify_job(cid,params['task_id'],self.jobs,params['job_id'],root,params.get('paths',[]))
            elif name == 'plan.update':
                if params.get('state')=='complete' and any(t['state']!='complete' for t in self.tasks.list(cid)):
                    raise ValueError('Structured tasks are unfinished or verification is stale; inspect task.list')
                result = self.catalog.plan(cid,params.get('plan'),params.get('goal'),params.get('state'))
            elif name.startswith('browser.'):
                result = self.browser.call(name,params,guard=browser_guard)
            elif name.startswith('desktop.'):
                result = self.desktop.call(name,{**params,'window_handle':params.get('window',params.get('window_handle'))},guard=execution_guard)
            else:
                project = context['project']
                if not project:
                    raise ValueError('Choose a project for this conversation before using PC tools')
                from .workspace import Workspace
                with self.lock:
                    if project['root'] not in self.workspaces:
                        self.workspaces[project['root']] = Workspace(project['root'])
                    workspace = self.workspaces[project['root']]
                if name == 'workspace.list':
                    result = workspace.list(params.get('path','.'))
                elif name == 'workspace.search':
                    result = workspace.search(params['query'],params.get('path','.'))
                elif name == 'git.status':
                    result = self.git.status(cid)
                elif name == 'job.start':
                    cwd = workspace.resolve(params.get('cwd','.'))
                    timeout=int(params.get('timeout_s',300))
                    if not 1 <= timeout <= 3600:
                        raise ValueError('timeout_s must be 1..3600')
                    prepared=None;task_id=params.get('verification_task_id')
                    if params.get('verification_paths') and not task_id:raise ValueError('verification_paths requires verification_task_id')
                    if task_id:prepared=self.tasks.prepare_job(cid,task_id,workspace.root,params.get('verification_paths',[]))
                    result = self.jobs.start(cid+':'+params['request_id'],params['command'],str(cwd),
                                             timeout_s=timeout)
                    with self.catalog.lock,self.catalog.db:
                        self.catalog.db.execute('INSERT OR IGNORE INTO job_links VALUES(?,?)',(result['job_id'],cid))
                    if prepared:self.tasks.link_job_verification(cid,task_id,result['job_id'],prepared)
                elif name.startswith('job.'):
                    job_id = params['job_id']
                    owned = self.catalog.rows('SELECT conversation_id FROM job_links WHERE job_id=?',(job_id,))
                    if not owned or owned[0]['conversation_id'] != cid:
                        raise ValueError('Job belongs to a different conversation')
                    if name == 'job.status':
                        result = self.jobs.status(job_id,int(params.get('cursor',0)))
                    elif name == 'job.wait':
                        result = self.jobs.wait(job_id,int(params.get('cursor',0)),min(20,int(params.get('timeout_s',20))))
                    else:
                        result = self.jobs.cancel(job_id)
            if name in ('desktop.screenshot','browser.screenshot'):
                try:
                    artifact=self.artifacts.import_file(cid,result['path'],source=name)
                    result={**result,'artifact_id':artifact['id'],'vision_delivery':'Saved locally. Owner can preview and explicitly attach it from Artifacts; returning a path does not send pixels.'}
                except (ValueError,OSError):
                    result={**result,'vision_delivery':'Saved locally but outside image attachment limits; no pixels sent.'}
                result.pop('path',None)  # Private snapshot paths stay inside the harness.
            # The callback boundary shapes the full envelope to 24,000 bytes.
            return {'ok':True,'payload':result}
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:
            raise

    def stop_all(self):
        self.paused = True
        self.jobs.set_paused(True)
        self.jobs.stop_all()
        self.mcp.stop_all()

    def run_ended_hooks(self,cid,run_id):
        try:self.hooks.run_event(cid,'run_ended',run_id)
        except Exception as exc:
            self.catalog.event(cid,'hook.dispatch_failed',{'run_id':run_id,'error_code':type(exc).__name__})

    def review_task(self,cid,task_id):
        """Explicit local owner action; deliberately absent from remote schemas."""
        record=None
        try:
            with self.execution.lock,self.execution.admission(cid,'task.update') as (effect,guard):
                guard();record=self.execution.start_call(cid,'task.review',effect)
                call_id,run_id,own=record;self.execution.call_started(cid,call_id,run_id,'task.review')
                result=self.tasks.accept_review(cid,task_id,note='Accepted using the local Tasks view')
                self.execution.finish_call(cid,call_id,run_id,'task.review','succeeded',own_run=own)
                return {'ok':True,'payload':result}
        except (ValueError,OSError,KeyError,RuntimeError) as exc:
            if record:self.execution.finish_call(cid,record[0],record[1],'task.review','unknown',type(exc).__name__,record[2])
            return {'ok':False,'error':{'message':str(exc)}}

    def resume(self):
        self.jobs.set_paused(False)
        self.paused = False

    def close(self):
        self.mcp.close()
        self.terminals.close()
        self.jobs.close()
        self.browser.close()
        self.desktop.close()
