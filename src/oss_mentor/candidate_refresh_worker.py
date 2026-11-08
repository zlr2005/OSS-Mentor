"""Bounded refresh while the platform server is running; never extends failed leases."""
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Callable

from oss_mentor.candidate_refresh import CandidateRefresher
from oss_mentor.storage.candidates import SQLiteCandidateStorage


class CandidateRefreshWorker:
    def __init__(self, store: SQLiteCandidateStorage, synchronizer_factory: Callable, *,
                 interval_seconds: int = 300, limit: int = 20):
        if interval_seconds < 1 or not 1 <= limit <= 100:
            raise ValueError('invalid refresh interval or batch size')
        self.store, self.factory = store, synchronizer_factory
        self.interval, self.limit = interval_seconds, limit
        self._stop, self._lock = Event(), Lock()
        self._thread = None
        self._state = dict(enabled=True, status='waiting', interval_seconds=interval_seconds, batch_limit=limit)

    def status(self):
        with self._lock:
            return dict(self._state)

    def run_once(self):
        now = datetime.now(timezone.utc)
        self.store.initialize()
        self.store.mark_stale_candidates(now=now)
        # Sustain approved candidates first. Historical records requiring a new
        # context review remain held until explicitly selected by the batch CLI.
        with self.store.connect() as c:
            rows = c.execute(
                "SELECT t.task_candidate_id,t.repository_id,t.issue_number,t.github_issue_id,r.full_name AS repository "
                "FROM task_candidate t JOIN repository r USING(repository_id) "
                "WHERE t.candidate_eligibility='eligible' AND "
                "COALESCE(julianday(t.verification_expires_at),julianday(t.github_verified_at,'+24 hours'),0)<=julianday(?) "
                "ORDER BY CASE WHEN json_extract(t.feature_evidence_json,'$.difficulty_assessment.information_quality.actionability')='actionable' "
                "THEN 0 ELSE 1 END, COALESCE(julianday(t.verification_expires_at),0),t.task_candidate_id LIMIT ?",
                ((now + timedelta(hours=1)).isoformat(), self.limit)).fetchall()
            names = {r['repository'] for r in rows}
            repos = [SimpleNamespace(**dict(r)) for r in c.execute('SELECT full_name,ecosystem,primary_language FROM repository') if r['full_name'] in names]
        sync = self.factory()
        result = CandidateRefresher(sync, self.store).refresh(repos, limit=self.limit,
            refresh_empty_repositories=False, selected_candidates=[dict(r) for r in rows])
        state = dict(enabled=True, status='rate_limited' if result.rate_limited else ('partial' if result.failed_count else 'completed'),
            interval_seconds=self.interval, batch_limit=self.limit, last_started_at=now.isoformat(),
            last_finished_at=datetime.now(timezone.utc).isoformat(), selected_count=len(rows),
            refreshed_count=result.refreshed_count, unavailable_count=result.unavailable_count,
            failed_count=result.failed_count, errors=list(result.errors))
        with self._lock:
            self._state = state
        return state

    def _run(self):
        while not self._stop.is_set():
            try:
                state = self.run_once()
                delay = max(self.interval, 900) if state['status'] == 'rate_limited' else self.interval
            except Exception as exc:
                # Only the error class is public; external messages may contain URLs or credentials.
                with self._lock:
                    self._state.update(status='failed', error_type=type(exc).__name__)
                delay = max(self.interval, 60)
            self._stop.wait(delay)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name='candidate-refresh', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
