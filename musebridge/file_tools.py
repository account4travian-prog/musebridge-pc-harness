"""Line-based file adapters with hidden version receipts and atomic publication."""
from __future__ import annotations

import copy
import json
import re

from .file_reads import FileReadStore
from .workspace import Workspace, MAX_WRITE_BYTES
from .workspace_text import FileTextError


class FileTools:
    """Bind observed versions to a chat while sharing guarded workspace access."""

    def __init__(self, router):
        self.router = router
        self.reads = FileReadStore(router.catalog)

    def workspace(self, cid):
        """Resolve the selected project without bypassing any Workspace guard."""
        context = self.router.catalog.context(cid)
        if not context['project']:
            raise FileTextError('no_project', 'Ask the owner to choose a project for this conversation.')
        root = context['project']['root']
        with self.router.lock:
            if root not in self.router.workspaces:
                self.router.workspaces[root] = Workspace(root)
            return self.router.workspaces[root]

    def call(self, name, cid, params, run_id,execution_guard=None):
        """Route one file operation and convert known prepublication denials."""
        workspace = self.workspace(cid)
        try:
            if name in ('file.read','file.edit','file.write'):
                # All downstream receipts, checkpoints and hook filters use
                # one project-relative identity, including legacy absolute
                # paths that the workspace guard permits inside this root.
                target=self._target(workspace,params.get('path'),write=name!='file.read')
                params={**params,'path':target.relative_to(workspace.root).as_posix()}
            if name == 'file.read':
                return self.read(workspace, cid, params)
            if name == 'file.edit':
                return self.edit(workspace, cid, params, run_id,execution_guard)
            if name == 'file.write':
                return self.write(workspace, cid, params, run_id,execution_guard)
            if name == 'file.list':
                from .workspace_index import list_tree
                return list_tree(workspace, params.get('path', '.'), params.get('depth', 2))
            if name == 'file.search':
                from .workspace_index import search_files
                if any(type(params.get(key, False)) is not bool for key in ('literal', 'ignore_case')):
                    raise FileTextError('bad_arguments', 'literal and ignore_case must be booleans.')
                return search_files(workspace, params.get('pattern'), params.get('path', '.'),
                    glob=params.get('glob'), mode=params.get('mode', 'content'),
                    context=params.get('context', 0), literal=params.get('literal', False),
                    ignore_case=params.get('ignore_case', False), max_results=params.get('max_results', 50))
            raise FileTextError('bad_arguments', 'Use file.read, file.edit, file.write, file.list, or file.search.')
        except FileTextError:
            raise
        except ValueError as exc:
            if name in ('file.edit', 'file.write'):
                raise
            message = str(exc)
            if 'SHA-256 mismatch' in message or 'file changed since' in message:
                raise FileTextError('changed_since_read', 'The file changed. Read it again before editing.') from None
            if any(text in message for text in ('escapes the selected workspace', 'private application data', 'credential storage')):
                raise FileTextError('path_outside_project', 'Use a relative path inside this project; private application data is excluded.') from None
            raise FileTextError('bad_arguments', message[:1000]) from None

    def _target(self, workspace, path, *, write=False):
        try:
            return workspace._validate_operation_path(path, write=write)
        except ValueError:
            raise FileTextError('path_outside_project',
                'Use a writable relative project path. Private data and .git writes are excluded.') from None

    def read(self, workspace, cid, params):
        """Show numbered text and remember exactly the byte version observed."""
        if 'offset' in params or 'length' in params:
            raise FileTextError('bad_arguments', 'file.read now uses start_line and max_lines. Read with start_line=1, max_lines=400.')
        path = params['path']
        self._target(workspace, path)
        start_line = params.get('start_line', 1)
        if type(start_line) is not int or not 1 <= start_line <= 2**31-1:
            raise FileTextError('bad_arguments', 'start_line must be a positive 32-bit line number.')
        path_info = self._path_info(workspace.resolve(path).relative_to(workspace.root).as_posix())
        budget = 20000 - len(json.dumps(path_info, ensure_ascii=True).encode()) - 512
        result, sha = workspace.text_page(path, start_line, params.get('max_lines', 400), budget=budget)
        if sha is not None:
            self.reads.remember(cid, workspace, path, sha)
        return {**path_info, **result}

    def _expect(self, workspace, cid, path, current, params, *, allow_overwrite=False):
        observed = self.reads.get(cid, workspace, path)
        if not allow_overwrite:
            if observed is None:
                raise FileTextError('not_read', 'Read the file first: file.read path=' + str(path))
            if observed != current:
                raise FileTextError('changed_since_read', 'The file changed since this chat read it. Read it again: file.read path=' + str(path))
        supplied = params.get('expected_sha256')
        if supplied is not None:
            if not isinstance(supplied, str) or not re.fullmatch('[a-fA-F0-9]{64}', supplied):
                raise FileTextError('bad_arguments', 'The optional legacy SHA-256 must contain exactly 64 hexadecimal characters.')
            if supplied.lower() != current:
                raise FileTextError('changed_since_read', 'The supplied legacy version is stale. Read the file again before editing.')
        return current

    def edit(self, workspace, cid, params, run_id,guard=None):
        """Apply one or many guarded edits through exactly one atomic replace."""
        path = params['path']
        with self.router.execution.lock,workspace._write_lock:
            self._publication_guard(guard)
            self._target(workspace, path, write=True)
            document, current = workspace.text_document(path)
            expected = self._expect(workspace, cid, path, current, params)
            singular = ('old', 'new', 'replace_all', 'start_line', 'end_line')
            if 'edits' in params:
                if any(key in params for key in singular):
                    raise FileTextError('bad_arguments', 'Use either edits=[...] or one old/new or line-range edit.')
                edits = params['edits']
            else:
                edits = [{key: params[key] for key in singular if key in params}]
            if not isinstance(edits, list) or not 1 <= len(edits) <= 50:
                raise FileTextError('bad_arguments', 'Provide 1..50 ordered edits.')
            for edit in edits:
                if isinstance(edit, dict) and any(key in edit for key in ('start_line', 'end_line')):
                    if any(key in edit for key in ('old', 'replace_all')):
                        raise FileTextError('bad_arguments', 'Each edit must use either old/new or a line range, not both.')
            try:
                data, result = document.apply_edits(edits)
            except FileTextError:
                raise
            except ValueError as exc:
                raise FileTextError('bad_arguments', str(exc)[:1000]) from None
            if len(data) > MAX_WRITE_BYTES:
                raise FileTextError('too_large', 'The edited file would exceed the 4 MiB limit.')
            result = self._publish(workspace, cid, path, data, expected, run_id, result)
        return self._after_publish(cid,path,data,result,guard)

    def write(self, workspace, cid, params, run_id,guard=None):
        """Create a text file, or replace an observed/explicitly allowed version."""
        text = params.get('text')
        overwrite = params.get('overwrite', False)
        create_only = params.get('create', False)
        if not isinstance(text, str) or type(overwrite) is not bool or type(create_only) is not bool:
            raise FileTextError('bad_arguments', 'Provide text as a string and overwrite/create as booleans.')
        path = params['path']
        with self.router.execution.lock,workspace._write_lock:
            self._publication_guard(guard)
            target = self._target(workspace, path, write=True)
            exists = target.exists()
            if exists and create_only:
                raise FileTextError('already_exists', 'The file already exists. Read it before replacing it.')
            if exists:
                document, current = workspace.text_document(path)
                expected = self._expect(workspace, cid, path, current, params, allow_overwrite=overwrite)
                try:
                    data = document.encode_text(text)
                except FileTextError:
                    raise
                except ValueError as exc:
                    raise FileTextError('bad_arguments', str(exc)[:1000]) from None
            else:
                if params.get('expected_sha256') is not None:
                    raise FileTextError('changed_since_read', 'The expected file no longer exists; inspect before recreating it.')
                normalized = text.replace('\r\n', '\n').replace('\r', '\n')
                if target.suffix.lower() in ('.ps1', '.bat', '.cmd'):
                    normalized = normalized.replace('\n', '\r\n')
                try:
                    data = normalized.encode('utf-8')
                except UnicodeError:
                    raise FileTextError('bad_arguments', 'Provide valid Unicode text without unpaired surrogate characters.') from None
                expected = None
            if len(data) > MAX_WRITE_BYTES:
                raise FileTextError('too_large', 'The text would exceed the 4 MiB file limit.')
            normalized = text.replace('\r\n','\n').replace('\r','\n')
            lines = normalized.count('\n') + bool(normalized and not normalized.endswith('\n'))
            result=self._publish(workspace, cid, path, data, expected, run_id, {'size': len(data), 'lines': lines}, create=not exists)
        return self._after_publish(cid,path,data,result,guard)

    def _publish(self, workspace, cid, path, data, expected, run_id, result, *, create=False):
        target = workspace._validate_operation_path(path, write=True)
        created = 0
        if create:
            missing = []
            parent = target.parent
            while parent != workspace.root and not parent.exists():
                missing.append(parent)
                if len(missing) > 128:
                    raise FileTextError('too_large', 'Create at most 128 new parent folders in one file.write call.')
                parent = parent.parent
            for parent in reversed(missing):
                workspace._validate_operation_path(parent, write=True)
                try:
                    parent.mkdir()
                    created += 1
                except FileExistsError:
                    if not parent.is_dir():
                        raise
            if created:
                self.router.catalog.event(cid, 'file.directories_created', {
                    'count': created, 'path': target.relative_to(workspace.root).as_posix()})
        identifier = self.router.checkpoints.capture_before(cid, run_id, workspace.root, path)
        try:
            target = workspace._validate_operation_path(path, write=True)
            try:
                published = workspace._atomic_replace(target, data, expected, create=create)
            except ValueError as exc:
                if 'SHA-256 mismatch' in str(exc) or 'file changed since' in str(exc):
                    raise FileTextError('changed_since_read', 'The file changed during publication. Read it again before editing.') from None
                raise
            self.reads.remember(cid, workspace, path, published['sha256'])
        finally:
            edit = self.router.checkpoints.finish(identifier)
            if edit['change_kind'] != 'unchanged':
                self.router.finish_line.changed(cid, run_id, path)
        metadata = {**result, 'path': published['path'], 'size': published['size'], 'edit_id': identifier}
        if created:
            metadata['created_directory_count'] = created
            metadata['directory_note'] = 'Checkpoints restore this file; newly created folders are retained.'
        payload = self._bounded_result(metadata)
        return payload

    @staticmethod
    def _publication_guard(guard):
        if guard:
            try:guard()
            except ValueError as exc:
                from .tool_results import normalize_error
                error=normalize_error(exc)
                raise FileTextError(error['code'],error['message']+' '+error['hint']) from None

    def _after_publish(self,cid,path,data,payload,guard):
        # Parsing and project checks run after publication, outside mutation
        # locks. Stop/profile changes must remain responsive during a slow check.
        try:
            from .edit_diagnostics import diagnose
            diagnostics=diagnose(path,data)
            if diagnostics:
                payload.setdefault('diagnostics',[]).extend(diagnostics)
                payload['diagnostic_warning']='The file does not parse after this edit.'
        except Exception:
            payload['feedback_warning']='Syntax feedback is unavailable; the file edit was saved.'
        try:
            with self.router.execution.lock:
                if guard:guard()
                invocation=self.router.hooks.run_event(cid,'after_file_edit',payload['edit_id'],path=path)
            payload['after_edit_hooks']=invocation
            check=self.router.edit_checks.after_edit(cid,path,payload['edit_id'],invocation,guard or (lambda:None))
            if check is not None:payload['check_on_edit']=check
        except ValueError as exc:
            from .tool_results import normalize_error
            error=normalize_error(exc)
            payload['feedback_warning']='The file edit was saved; after-edit checks were not confirmed. '+error['message']+' '+error['hint']
        except Exception:
            payload['feedback_warning']='After-edit feedback is unavailable; the file edit was saved. Inspect configured hooks before retrying any check.'
        return payload

    def _bounded_result(self, result):
        payload = copy.deepcopy(result)
        if 'path' in payload:
            path = payload.pop('path')
            payload.update(self._path_info(path))
        while len(json.dumps(payload, ensure_ascii=True).encode()) > 20000:
            regions = payload.get('changed_regions', [])
            if len(regions) > 1:
                regions.pop(len(regions) // 2)
                payload['regions_omitted'] = payload.get('regions_omitted', 0) + 1
            elif regions:
                payload['changed_regions'] = []
                payload['regions_omitted'] = payload.get('regions_omitted', 0) + 1
            else:
                payload['diagnostics'] = []
                break
            payload['truncated'] = True
            payload['hint'] = 'Use file.read to inspect the complete changed regions.'
        return payload

    def _path_info(self, path):
        if len(json.dumps(path, ensure_ascii=True).encode()) <= 2000:
            return {'path': path}
        keep = min(len(path), 1000)
        preview = path
        while len(json.dumps(preview, ensure_ascii=True).encode()) > 2000:
            preview = path[:keep // 2] + '…' + path[-keep // 2:]
            keep //= 2
        return {'path_preview': preview, 'path_truncated': True,
                'hint': 'Reuse the original requested path; this displayed path is shortened.'}
