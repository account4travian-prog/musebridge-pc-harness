"""Byte-preserving run snapshots in a private Git object store, never project Git."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .workspace import Workspace
from .workspace_index import _spec, _ignored

MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 100_000
MAX_TOTAL = 256 * 1024 * 1024
EXCLUDE = {'.git','.local','local-data','credentials','credential','node_modules','.venv',
           '__pycache__','.pytest_cache','dist','build','target','.next','.mypy_cache'}


class ShadowGit:
    """Snapshot files only; external effects and excluded/oversize files are not undone."""
    def __init__(self, router, directory):
        self.router = router
        self.catalog = router.catalog
        self.directory = Path(directory).resolve()
        self.base = self.directory / 'shadow'
        self.lock = threading.RLock()
        self.git = shutil.which('git')
        self.cache = {}
        self.tree_cache = {}

    def _repo(self, project_id):
        return self.base / (project_id + '.git')

    def _git(self, repo, root, *args, data=None):
        if not self.git:
            raise OSError('Git unavailable; direct file checkpoints remain available')
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_DIR=str(repo), GIT_WORK_TREE=str(root), GIT_CONFIG_NOSYSTEM='1',
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
        if args and args[0]=='init':env.pop('GIT_WORK_TREE',None)
        result = subprocess.run([self.git,'-c','core.hooksPath='+os.devnull,*args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=30, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise OSError('Private Git snapshot operation failed; direct file checkpoints remain available')
        return result.stdout

    @staticmethod
    def _regular(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
            raise ValueError('Snapshot paths cannot be links or reparse points')
        return info

    def _target(self, root, relative):
        original = Path(root)
        if original.resolve(strict=True) != original:
            raise ValueError('Snapshot workspace identity changed')
        parts = Path(relative).parts
        if Path(relative).is_absolute() or '..' in parts or not parts or any(p.casefold() in EXCLUDE for p in parts):
            raise ValueError('Snapshot path is outside the allowed project files')
        current = original
        self._regular(current)
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():self._regular(current)
        return Workspace(root)._validate_operation_path(relative,write=True)

    def _files(self, root):
        deadline = time.monotonic()+60
        stack = [(Path(root),[])]
        count = 0
        while stack:
            parent, specs = stack.pop()
            self._regular(parent)
            local = _spec(parent)
            specs = specs + ([(parent,local)] if local else [])
            with os.scandir(parent) as entries:
                for entry in entries:
                    if time.monotonic()>deadline:raise ValueError('Snapshot scan exceeded its 60-second limit')
                    if entry.name.casefold() in EXCLUDE:continue
                    path = Path(entry.path)
                    if path.suffix.casefold() in ('.sqlite','.sqlite3','.db','.mdb','.accdb') or entry.name.endswith(('-wal','-shm','-journal')):continue
                    if path == self.directory or self.directory in path.parents:continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:continue
                    is_dir = stat.S_ISDIR(info.st_mode)
                    if _ignored(specs,path,is_dir):continue
                    if is_dir:
                        stack.append((path,specs))
                    elif stat.S_ISREG(info.st_mode) and info.st_size<=MAX_FILE:
                        count+=1
                        if count>MAX_FILES:raise ValueError('Over 100000 snapshot candidates; direct file checkpoints remain available')
                        yield path,info

    def snapshot(self, project_id, root):
        """Create an exact-byte tree without filters, hooks, checkout, or project index writes."""
        if not self.git:raise OSError('Git unavailable; direct file checkpoints remain available')
        repo = self._repo(project_id)
        with self.lock:
            self.base.mkdir(parents=True,exist_ok=True)
            self._regular(self.base)
            if not repo.exists():
                self._git(repo,root,'init','--bare',str(repo))
            self._regular(repo)
            records=list(self._files(root))
            cached=self.cache.get(project_id,{})
            unchanged=(len(records)==len(cached) and all(
                cached.get(path.relative_to(root).as_posix(),(None,))[0]==
                (info.st_size,info.st_mtime_ns,info.st_ctime_ns) for path,info in records))
            tree=self.tree_cache.get(project_id)
            if tree and unchanged:
                self._git(repo,root,'cat-file','-e',tree)
                return tree
            try:parent=self._git(repo,root,'rev-parse','--verify','refs/heads/snapshots').strip()
            except OSError:parent=None
            # fast-import inline blobs avoid worktree attributes/filters and preserve CRLF.
            total = 0
            workspace=Workspace(root)
            workspace._validate_operation_path('.')
            current_cache={}
            with tempfile.TemporaryFile() as stream:
                stream.write(b'commit refs/heads/snapshots\ncommitter MuseBridge <local@invalid> 1 +0000\ndata 8\nsnapshot\n')
                if parent:stream.write(b'from '+parent+b'\n')
                stream.write(b'deleteall\n')
                for path,info,data,blob in self._contents(root,cached,records):
                    relative = path.relative_to(root).as_posix()
                    signature=(info.st_size,info.st_mtime_ns,info.st_ctime_ns)
                    quoted=json.dumps(relative,ensure_ascii=False).encode('utf-8')
                    if data is None:
                        total+=info.st_size
                        if total>MAX_TOTAL:raise ValueError('Snapshot exceeds the 256 MiB run budget')
                        stream.write(b'M 100644 '+blob.encode()+b' '+quoted+b'\n')
                        current_cache[relative]=(signature,blob)
                        continue
                    total+=len(data)
                    if total>MAX_TOTAL:raise ValueError('Snapshot exceeds the 256 MiB run budget')
                    stream.write(b'M 100644 inline '+quoted+b'\ndata '+str(len(data)).encode()+b'\n'+data+b'\n')
                    current_cache[relative]=(signature,blob)
                stream.write(b'\ndone\n')
                stream.seek(0)
                env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
                env.update(GIT_DIR=str(repo),GIT_WORK_TREE=str(root),GIT_CONFIG_NOSYSTEM='1',
                           GIT_CONFIG_GLOBAL=os.devnull,GIT_ATTR_NOSYSTEM='1')
                result=subprocess.run([self.git,'fast-import','--quiet','--done'],stdin=stream,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=30,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise OSError('Private snapshot import failed')
            self.cache[project_id]=current_cache
            tree=self._git(repo,root,'rev-parse','refs/heads/snapshots^{tree}').decode().strip()
            self.tree_cache[project_id]=tree
            return tree

    @staticmethod
    def _read_record(record):
        path,info=record
        with path.open('rb') as source:
            if os.name=='nt':
                import msvcrt
                import win32file
                actual=win32file.GetFinalPathNameByHandle(msvcrt.get_osfhandle(source.fileno()),0)
                if Path(actual.removeprefix('\\\\?\\'))!=path:raise ValueError('Snapshot file identity changed during open')
            elif path.resolve()!=path:raise ValueError('Snapshot file identity changed during open')
            data=source.read(MAX_FILE+1)
            after=os.fstat(source.fileno())
        if (len(data)>MAX_FILE or (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)
                or info.st_ino and info.st_ino!=after.st_ino):
            raise ValueError('A file changed during snapshot; recovery is unavailable for this run')
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        return path,info,data,blob

    def _contents(self,root,cached,records):
        # Scan before I/O so a busy disk cannot consume the directory-scan budget.
        pending=[]
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='SnapshotRead') as pool:
            for path,info in records:
                relative=path.relative_to(root).as_posix()
                old=cached.get(relative)
                if old and old[0]==(info.st_size,info.st_mtime_ns,info.st_ctime_ns):
                    yield path,info,None,old[1]
                else:pending.append((path,info))
                if len(pending)==8:
                    yield from pool.map(self._read_record,pending)
                    pending=[]
            if pending:yield from pool.map(self._read_record,pending)

    def begin(self, cid, run_id):
        """Capture run-start state before tools can mutate the workspace."""
        run = self.router.execution.run(cid,run_id)
        if not run['project_id']:return
        project = self.catalog.project(run['project_id'])
        with self.lock:
            if self.catalog.rows('SELECT run_id FROM shadow_runs_v1 WHERE run_id=?',(run_id,)):return
            tree,note = None,''
            try:tree=self.snapshot(project['id'],Path(project['root']))
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                from .tool_results import normalize_error
                note=normalize_error(exc)['message'][:220]
            with self.catalog.lock,self.catalog.db:
                self.catalog.db.execute('INSERT INTO shadow_runs_v1 VALUES(?,?,?,?,?,?,?,?)',
                    (run_id,cid,project['id'],project['root'],tree,None,'active' if tree else 'unavailable',note))

    def finish(self,cid,run_id):
        """Record the terminal tree; uncertain/active effects cannot become restore evidence."""
        with self.lock:
            rows=self.catalog.rows('SELECT * FROM shadow_runs_v1 WHERE run_id=? AND conversation_id=?',(run_id,cid))
            if not rows or rows[0]['state']!='active':return
            row=rows[0]
            state=self.router.execution.run(cid,run_id)['state']
            tree,note=None,''
            try:
                if state in ('unknown','interrupted'):raise ValueError('Run outcome uncertain; automatic restore evidence is unavailable')
                if self.router.shell._list(cid)['jobs']:raise ValueError('Commands still running at run end; use direct file checkpoints')
                tree=self.snapshot(row['project_id'],Path(row['root']))
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                from .tool_results import normalize_error
                note=normalize_error(exc)['message'][:220]
            with self.catalog.lock,self.catalog.db:
                self.catalog.db.execute('UPDATE shadow_runs_v1 SET end_tree=?,state=?,note=? WHERE run_id=?',
                    (tree,'finished' if tree else 'unavailable',note,run_id))
            if tree:
                for item in self.review(cid,run_id)['edits']:
                    import hashlib
                    with self.router.checkpoints._connection() as db:
                        edit=db.execute('SELECT after_sha256,after_exists FROM edits WHERE run_id=? AND relative_path=? '
                                        'ORDER BY captured_at DESC LIMIT 1',(run_id,item['path'])).fetchone()
                    final=self._blob(row,tree,item['path'])
                    if not edit or (edit['after_sha256']!=hashlib.sha256(final).hexdigest() if final is not None else bool(edit['after_exists'])):
                        self.router.finish_line.changed(cid,run_id,item['path'])

    def rebase(self,cid,run_id):
        """Capture the first write baseline after acquiring the shared-root ownership lease."""
        self.begin(cid,run_id)
        with self.lock:
            row=self._row(cid,run_id)
            if not row or row['state']!='active':return
            try:
                tree=self.snapshot(row['project_id'],Path(row['root']))
                with self.catalog.lock,self.catalog.db:
                    self.catalog.db.execute('UPDATE shadow_runs_v1 SET start_tree=? WHERE run_id=?',(tree,run_id))
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                from .tool_results import normalize_error
                with self.catalog.lock,self.catalog.db:
                    self.catalog.db.execute("UPDATE shadow_runs_v1 SET state='unavailable',note=? WHERE run_id=?",
                        (normalize_error(exc)['message'][:220],run_id))

    def _row(self,cid,run_id):
        rows=self.catalog.rows('SELECT * FROM shadow_runs_v1 WHERE run_id=? AND conversation_id=?',(run_id,cid))
        if not rows:return None
        row=rows[0]
        if row['project_id']!=self.catalog.conversation(cid)['project_id']:raise ValueError('Snapshot belongs to the previous workspace')
        if self.catalog.project(row['project_id'])['root']!=row['root']:raise ValueError('Snapshot workspace root changed')
        return row

    def _blob(self,row,tree,path):
        repo=self._repo(row['project_id'])
        exists=self._git(repo,row['root'],'ls-tree','-z',tree,'--',path)
        if not exists:return None
        if not exists.startswith(b'100644 blob '):raise ValueError('Snapshot entry is not a regular file')
        data=self._git(repo,row['root'],'cat-file','blob',tree+':'+path)
        if len(data)>MAX_FILE:raise ValueError('Snapshot blob exceeds restore limit')
        return data

    def latest(self,cid,path=None):
        """Find recent completed run evidence in the currently selected project."""
        project=self.catalog.conversation(cid)['project_id']
        for item in self.catalog.rows("SELECT run_id FROM shadow_runs_v1 WHERE conversation_id=? AND project_id=? "
                                     "AND state='finished' ORDER BY rowid DESC LIMIT 100",(cid,project)):
            if path is None or self.review(cid,item['run_id'],path)['edits']:
                return item['run_id']
        return None

    @staticmethod
    def _current(target):
        if not target.exists():return None
        info=target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_FILE:
            raise ValueError('Later owner edit or oversized file: restore conflict')
        with target.open('rb') as stream:
            data=stream.read(MAX_FILE+1)
        if len(data)>MAX_FILE:raise ValueError('File grew during restore: conflict')
        return data

    def review(self,cid,run_id,path=None):
        """Return bounded file diffs or an explicit fallback note."""
        row=self._row(cid,run_id)
        if not row:return None
        if row['state']!='finished':return {'run_id':run_id,'edits':[],'note':row['note'] or 'Run has not finished; use direct file checkpoints'}
        raw=self._git(self._repo(row['project_id']),row['root'],'diff','--name-status','-z','--no-renames',row['start_tree'],row['end_tree']).decode('utf-8').split('\0')
        edits=[]
        for index in range(0,len(raw)-1,2):
            kind,name=raw[index:index+2]
            if path is not None and path!=name:continue
            if len(edits)>=100:break
            before=self._blob(row,row['start_tree'],name)
            after=self._blob(row,row['end_tree'],name)
            if b'\0' in (before or b'')+(after or b''):diff='Binary file change'
            else:diff=''.join(difflib.unified_diff((before or b'').decode('utf-8','replace').splitlines(True),
                (after or b'').decode('utf-8','replace').splitlines(True),fromfile='a/'+name,tofile='b/'+name))[:5000]
            try:restored=self._current(self._target(row['root'],name))==before
            except (OSError,ValueError):restored=False
            edits.append({'path':name,'run_id':run_id,'source':'shadow_git','restored':restored,
                          'change_kind':{'A':'created','D':'deleted'}.get(kind,'modified'),'diff':diff})
        return {'run_id':run_id,'edits':edits,'count':len(edits),'path':path,
                'truncated':path is None and len(raw)>202,'note':row['note']}

    def restore(self,cid,run_id,path,guard):
        """Preflight all selected files and refuse later owner edits before any restore."""
        with self.lock:
            row=self._row(cid,run_id)
            if not row or row['state']!='finished':return None
            # Preserve current state separately; never redefine the run-end comparison.
            self.snapshot(row['project_id'],Path(row['root']))
            review=self.review(cid,run_id,path)
            if review['truncated']:raise ValueError('Restore has over 100 files; narrow by path')
            pending=[]
            for item in review['edits']:
                target=self._target(row['root'],item['path'])
                before=self._blob(row,row['start_tree'],item['path'])
                after=self._blob(row,row['end_tree'],item['path'])
                current=self._current(target)
                if current==before:continue
                if current!=after:raise ValueError('Later owner edit: restore conflict; no files restored')
                pending.append((item['path'],target,before,after))
            restored=[]
            for name,target,before,after in pending:
                guard()
                self._target(row['root'],name)
                current=self._current(target)
                if current!=after:raise ValueError('File changed during restore; inspect partial restored list in Changes')
                if before is None:target.unlink()
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    fd,temp=tempfile.mkstemp(prefix='.restore-',dir=target.parent)
                    try:
                        with os.fdopen(fd,'wb') as stream:
                            stream.write(before)
                            stream.flush()
                            os.fsync(stream.fileno())
                        self._target(row['root'],name)
                        if self._current(target)!=after:raise ValueError('Later owner edit: restore conflict')
                        os.replace(temp,target)
                    finally:Path(temp).unlink(missing_ok=True)
                self.router.finish_line.changed(cid,self.router.execution.active_run(cid) or run_id,name)
                restored.append({'path':name,'source':'shadow_git'})
            return {'run_id':run_id,'restored':restored,'failed':[],'all_succeeded':True}
