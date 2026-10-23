"""Seven-day raw retention, independent of cookies and retained documents."""
from datetime import datetime, timedelta, timezone
from threading import RLock

from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.deletion import DeletionService


class RetentionService:
    def __init__(self, *, repository: ChatRepository, deletion: DeletionService, clock=None):
        self._repository = repository
        self._deletion = deletion
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self.pending_count = 0
        self.cleaned_count = 0

    def sweep(self) -> int:
        """Retry interrupted cleanup; fail closed if expired raw cannot be removed."""
        with self._lock:
            self._deletion.reconcile_cleanup()
            now = self._clock().astimezone(timezone.utc)
            removed = 0
            pending = self._deletion.reconcile_export_temporaries()
            for target in self._repository.raw_retention_targets():
                created = datetime.fromisoformat(str(target["created_at_utc"]))
                if created + timedelta(days=7) > now:
                    continue
                try:
                    self._deletion.expire_raw(target_kind=str(target["target_kind"]),
                        target_id=str(target["target_id"]), session_hash=str(target["session_digest"]))
                    removed += 1

                except (OSError, PermissionError, ValueError):
                    # No bodies, keys or file paths enter the status or logs.
                    pending += 1
            pending += len(self._repository.pending_deletion_cleanups())
            pending += len(self._repository.interrupted_deletion_staging())
            self.pending_count = pending
            self.cleaned_count += removed
            return removed
