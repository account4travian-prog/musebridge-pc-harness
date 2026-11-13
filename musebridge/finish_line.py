"""Revision-bound check feedback and one optional queued follow-up per request."""
from __future__ import annotations


class FinishLine:
    def __init__(self, router):
        self.router = router
        self.catalog = router.catalog

    def configuration(self, cid):
        """Return owner-only, project-scoped settings; automatic sends default off."""
        project_id = self.catalog.conversation(cid)['project_id']
        return (self.catalog.preference('finish_check:' + project_id, default=None)
                if project_id else None) or {'enabled': False, 'check_command': ''}

    def configure(self, cid, command, enabled=False):
        """Save settings locally without executing the command or resuming a queue."""
        project = self.catalog.context(cid).get('project')
        if not project:
            raise ValueError('Choose a project before configuring a finish-line check')
        if (type(enabled) is not bool or not isinstance(command, str)
                or len(command.encode()) > 8000 or (enabled and not command.strip())):
            raise ValueError('Provide a check command of at most 8000 bytes before enabling auto-check')
        state = {'enabled': enabled, 'check_command': command.strip()}
        self.catalog.preference('finish_check:' + project['id'], state)
        return state

    def bind_run(self, cid, run_id, source_run=None, goal=False):
        """Preserve lineage across machine continuations, including goal rounds."""
        with self.catalog.lock, self.catalog.db:
            self.router.execution.run(cid, run_id)
            existing = self.catalog.rows('SELECT request_id FROM finish_runs_v1 WHERE run_id=?', (run_id,))
            if existing:
                return existing[0]['request_id']
            request_id = run_id
            if source_run:
                self.router.execution.run(cid, source_run)
                request_id = self._request(source_run)
            self.catalog.db.execute(
                'INSERT OR IGNORE INTO finish_requests_v1(id,round_limit) VALUES(?,?)',
                (request_id, 5 if goal else 0))
            if goal:
                self.catalog.db.execute('UPDATE finish_requests_v1 SET round_limit=5 WHERE id=?', (request_id,))
            inserted = self.catalog.db.execute(
                'INSERT OR IGNORE INTO finish_runs_v1 VALUES(?,?)', (run_id, request_id)).rowcount
            if inserted:
                self.catalog.db.execute('UPDATE finish_requests_v1 SET rounds=rounds+1 WHERE id=?', (request_id,))
        return request_id

    def _request(self, run_id):
        rows = self.catalog.rows('SELECT request_id FROM finish_runs_v1 WHERE run_id=?', (run_id,))
        return rows[0]['request_id'] if rows else run_id

    def changed(self, cid, run_id, path):
        """Record a published mutation before any diagnostic or hook waits."""
        with self.catalog.lock, self.catalog.db:
            request_id = self.bind_run(cid, run_id)
            self.catalog.db.execute('UPDATE finish_requests_v1 SET revision=revision+1 WHERE id=?', (request_id,))
            revision = self.catalog.db.execute('SELECT revision FROM finish_requests_v1 WHERE id=?', (request_id,)).fetchone()[0]
            self.catalog.db.execute('INSERT OR REPLACE INTO finish_changes_v1 VALUES(?,?,?,?)',
                                    (request_id, run_id, path, revision))

    def watch_check(self, cid, run_id, job_id, require_receipt=False):
        """Bind a new command's start revision; retries cannot refresh old evidence."""
        with self.catalog.lock, self.catalog.db:
            request_id = self.bind_run(cid, run_id)
            self.catalog.db.execute('INSERT OR IGNORE INTO finish_checks_v1 '
                                    'SELECT ?,id,revision,? FROM finish_requests_v1 WHERE id=?',
                                    (job_id, int(not require_receipt), request_id))

    def approve_check(self, job_id):
        """A task check counts only after its verification adapter accepts the receipt."""
        with self.catalog.lock, self.catalog.db:
            self.catalog.db.execute('UPDATE finish_checks_v1 SET approved=1 WHERE job_id=?', (job_id,))

    def summary(self, cid, run_id=None):
        """Report bounded change feedback; successful commands are not task receipts."""
        run_id = run_id or self.router.execution.active_run(cid)
        if not run_id:
            rows = self.catalog.rows("SELECT id FROM runs WHERE conversation_id=? AND project_id IS ? "
                                     "AND origin!='tool' ORDER BY created DESC LIMIT 1",
                                     (cid, self.catalog.conversation(cid)['project_id']))
            run_id = rows[0]['id'] if rows else None
        if not run_id:
            return {'run_id': None, 'unverified_changes': 0, 'files_changed': 0}
        self.router.execution.run(cid, run_id)
        request_id = self._request(run_id)
        with self.catalog.lock, self.catalog.db:
            for check in self.catalog.rows('SELECT * FROM finish_checks_v1 WHERE request_id=? AND approved=1', (request_id,)):
                status = self.router.jobs.status(check['job_id'], 0)
                if status['state'] == 'succeeded' and status.get('exit_code') == 0:
                    self.catalog.db.execute('UPDATE finish_requests_v1 SET checked=max(checked,?) WHERE id=?',
                                            (check['revision'], request_id))
            rows = self.catalog.rows('SELECT * FROM finish_requests_v1 WHERE id=?', (request_id,))
            if not rows:
                return {'run_id': run_id, 'unverified_changes': 0, 'files_changed': 0}
            state = rows[0]
            files = self.catalog.rows('SELECT path,revision FROM finish_changes_v1 WHERE request_id=?', (request_id,))
        return {'run_id': run_id, 'request_id': request_id,
                'files_changed': len(files),
                'unverified_changes': sum(item['revision'] > state['checked'] for item in files),
                'revision': state['revision'], 'checked_revision': state['checked'],
                'queued': state['queued'], 'rounds': state['rounds'], 'round_limit': state['round_limit']}

    def eligible(self, cid, run_id):
        """Require an attributed, current, finished request and unchanged local policy."""
        run = self.router.execution.run(cid, run_id)
        conversation = self.catalog.conversation(cid)
        policy = self.router.execution.policy(cid)
        latest = self.catalog.rows("SELECT id FROM runs WHERE conversation_id=? AND origin!='tool' "
                                   'ORDER BY created DESC LIMIT 1', (cid,))
        config = self.configuration(cid)
        return (cid != 'main' and bool(conversation['remote_id']) and not conversation['archived']
                and not policy['paused'] and policy['profile'] != 'read_only'
                and run['project_id'] == conversation['project_id']
                and run['scope_revision'] == policy['revision']
                and run['state'] in ('remote_finished', 'remote_reply_finished')
                and latest and latest[0]['id'] == run_id
                and config['enabled'] and bool(config['check_command']))

    def queue_after_run(self, cid, run_id, queue):
        """Atomically reserve at most one prompt; never lift owner/restart holds."""
        with self.router.execution.lock, self.catalog.lock, self.catalog.db:
            if not self.eligible(cid, run_id) or cid in queue.held:
                return None
            summary = self.summary(cid, run_id)
            if (not summary['unverified_changes'] or summary['queued']
                    or summary['round_limit'] and summary['rounds'] >= summary['round_limit']):
                return None
            command = self.configuration(cid)['check_command']
            text = (f"You changed {summary['files_changed']} files and no check has run since. "
                    f"Run `{command}` and fix what fails, or say why it does not apply.")
            identifier = queue.add(cid, text, automatic_run=run_id, automatic_request=summary['request_id'])
            self.catalog.db.execute('UPDATE finish_requests_v1 SET queued=? WHERE id=?',
                                    (identifier, summary['request_id']))
            return identifier

    def delivery_allowed(self, cid, run_id):
        """Recheck pending automation after holds, new requests, checks or settings edits."""
        if not self.eligible(cid, run_id):
            return False
        state = self.summary(cid, run_id)
        entries = self.catalog.rows('SELECT text FROM request_queue_v1 WHERE automatic_request=?',
                                    (state.get('request_id'),))
        command = self.configuration(cid)['check_command']
        expected_text = (f"You changed {state['files_changed']} files and no check has run since. "
                         f"Run `{command}` and fix what fails, or say why it does not apply.")
        if not entries or entries[0]['text'] != expected_text:
            return False
        return bool(state['unverified_changes'] and
                    (not state['round_limit'] or state['rounds'] < state['round_limit']))
