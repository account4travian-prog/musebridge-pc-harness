"""Bounded, privacy-preserving desktop attention event aggregation."""
import time

KINDS=('run.remote_finished','run.remote_reply_finished','run.failed','run.unknown','hook.admission_unknown')


class AttentionTracker:
    def __init__(self,worker):
        self.worker=worker;self.seq=worker.catalog.rows('SELECT coalesce(max(seq),0) AS seq FROM events')[0]['seq']
        self.jobs={r['job_id'] for r in worker.router.jobs.finished_summary()};self.pending=set();self.last=-10**12

    def poll(self,now=None):
        now=time.monotonic() if now is None else now
        rows=self.worker.catalog.rows('SELECT seq,conversation_id,kind FROM events WHERE seq>? ORDER BY seq LIMIT 200',(self.seq,))
        for row in rows:
            self.seq=row['seq']
            if row['kind'] in KINDS:self.pending.add(row['conversation_id'])
        jobs=self.worker.router.jobs.finished_summary()
        for job in jobs:
            if job['job_id'] not in self.jobs:
                owner=self.worker.catalog.rows('SELECT conversation_id FROM job_links WHERE job_id=?',(job['job_id'],))
                if owner:self.pending.add(owner[0]['conversation_id'])
        self.jobs={r['job_id'] for r in jobs}
        if not self.pending or now-self.last<15:return None
        cids=sorted(self.pending);self.pending.clear();self.last=now
        return {'conversation_id':cids[0] if len(cids)==1 else '__overview__','count':len(cids)}
