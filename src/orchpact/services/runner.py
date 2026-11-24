"""One bounded local scheduler; restart recovers receipts before new work."""
import logging
import threading
import time

from ..adapters.database import now
from .executor import CrewAIExecutor


class Runner:
    def __init__(self, service, executor=None):
        self.service = service
        self.executor = executor or CrewAIExecutor(service)
        self.stop_event = threading.Event()
        self.service.stop_event = self.stop_event
        self.threads = []
        self.last_recovery = 0
        self.last_janitor = None

    def recover(self):
        self.service.tools.recover()
        from .agent_steps import recover_steps
        recover_steps(self.service)
        providers = getattr(self.executor, 'providers', None)
        if providers:
            providers.recover_receipts()
        # Finalizing a recovered child can publish a new root candidate.
        with self.service.db.connect() as c:
            interrupted_attempts = [row['id'] for row in c.execute("SELECT id FROM attempts WHERE status='running'")]
        self.service.verifications.recover()
        with self.service.db.tx() as c:
            c.execute("UPDATE attempts SET status='interrupted',finished=%s WHERE status='running' AND id=ANY(%s)",
                      (now(), interrupted_attempts))
            c.execute("""UPDATE tasks SET status='interrupted',error='执行中断，请先核对真实回执再明确重试'
                      WHERE status='running' AND attempt_id=ANY(%s)""", (interrupted_attempts,))
            c.execute("UPDATE requests SET status='unknown' WHERE status='started'")
            for row in c.execute("SELECT id FROM requests WHERE kind='search' AND status='unknown'").fetchall():
                self.service.access.wallets.settle(c, row['id'])

    def step(self):
        if self.stop_event.is_set():
            return False
        providers = getattr(self.executor, 'providers', None)
        if providers and time.monotonic() - self.last_recovery > 60:
            providers.recover_receipts()
            self.last_recovery = time.monotonic()
        if self.last_janitor is None or time.monotonic()-self.last_janitor>3600:
            self.service.janitor()
            self.last_janitor=time.monotonic()
        if self.service.verifications.step():return True
        ctx = self.service.claim()
        if not ctx:
            return False
        try:
            self.executor.execute(ctx)
        except Exception as exc:
            # Public errors carry types/validation only, never transport bodies or credentials.
            error = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
            try:
                self.service.fail(ctx['execution_context'], ctx['task']['id'], ctx['task']['attempt_id'], error)
            except ValueError:
                pass
        else:
            self.service.verifications.step()
        return True

    def start(self):
        if self.threads:
            return
        self.recover()
        self.stop_event.clear()

        def loop():
            while not self.stop_event.is_set():
                try:
                    if not self.step():
                        self.stop_event.wait(0.5)
                except Exception:
                    logging.error('scheduler pass failed', exc_info=False)
                    self.stop_event.wait(1)

        self.threads = [threading.Thread(target=loop, name=f'orchpact-worker-{i}', daemon=True)
                        for i in range(self.service.settings.workers)]
        for thread in self.threads:
            thread.start()

    def healthy(self):
        return not self.stop_event.is_set() and len(self.threads) == self.service.settings.workers and all(t.is_alive() for t in self.threads) and self.service.db.healthy()

    def stop(self, timeout=150):
        self.stop_event.set()
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self.threads):
            raise TimeoutError('执行器尚未排空，保留实例锁')
