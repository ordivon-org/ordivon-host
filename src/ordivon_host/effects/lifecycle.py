from __future__ import annotations

from collections.abc import Callable

from anc_canonical import JsonValue, canonical_digest, validate_json_value

from ..domain import EventKind, TaskDescriptor, TaskProjection, TaskState
from ..executors import DeliveryUncertain, EffectExecutor
from ..journal import JournalCorruption
from ..kernel import HostKernel, worker_owner_id
from ..objects import ObjectCorrupt, StoredObject
from ..storage import HostStorage, TaskEventSnapshot
from .models import (
    DispatchEnvelope,
    EffectStep,
    ObservationEnvelope,
    PreparedDispatch,
    TaskOutcome,
    VerificationReceipt,
)


class EffectLifecycleError(RuntimeError):
    pass


class EffectSuperseded(EffectLifecycleError):
    pass


class EffectLifecycleHost:
    """Executor-neutral Effect delivery with durable prepare/observe/verify boundaries."""

    def __init__(
        self,
        storage: HostStorage,
        *,
        clock_ms: Callable[[], int],
        owner_id: str | None = None,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if owner_id is not None and (not owner_id or owner_id != owner_id.strip()):
            raise ValueError("explicit Host owner identity must be trimmed")
        if lease_ttl_ms < 1:
            raise ValueError("Effect lifecycle lease TTL must be positive")
        self.storage = storage
        self.kernel = HostKernel(
            storage,
            clock_ms=clock_ms,
            owner_id=owner_id or worker_owner_id("host:effect-lifecycle-v1"),
            lease_ttl_ms=lease_ttl_ms,
        )

    def create_task(
        self,
        descriptor: TaskDescriptor,
        *,
        frontier: str,
        event_id: str | None = None,
    ) -> TaskProjection:
        if not frontier.startswith("node:"):
            raise ValueError("Task frontier must start with node:")
        descriptor_object = self.storage.put_object(
            descriptor.to_dict(), kind="task-descriptor"
        )
        existing = self.storage.journal.get_task(descriptor.task_id)
        if existing is not None:
            retained = self.storage.read_task_descriptor(descriptor.task_id)
            if retained != descriptor:
                raise ValueError("Task identity is bound to another descriptor")
            return existing
        receipt = self.kernel.create_task(
            event_id=event_id or f"event:{self._token(descriptor.task_id)}:created",
            kind=EventKind.TASK_CREATED,
            task_id=descriptor.task_id,
            goal_id=descriptor.goal_id,
            payload={
                "descriptorDigest": descriptor.digest,
                "descriptorObjectDigest": descriptor_object.digest,
            },
            frontier=(frontier,),
            referenced_objects=(descriptor_object,),
        )
        return receipt.projection

    def prepare(
        self,
        *,
        task_id: str,
        prepare_frontier: str,
        reconcile_frontier: str,
        verify_frontier: str,
        result_frontier: str,
        effect: dict[str, JsonValue],
        request: dict[str, JsonValue],
        dispatch: DispatchEnvelope,
        event_id: str | None = None,
    ) -> PreparedDispatch:
        validate_json_value(effect)
        validate_json_value(request)
        if effect.get("effectId") != dispatch.effect_id:
            raise ValueError("Effect identity differs from Dispatch")
        if canonical_digest(request) != dispatch.request_digest:
            raise ValueError("executor request digest differs from Dispatch")
        for frontier in (
            prepare_frontier,
            reconcile_frontier,
            verify_frontier,
            result_frontier,
        ):
            if not frontier.startswith("node:"):
                raise ValueError("Effect lifecycle frontiers must start with node:")
        current = self._require_frontier(task_id, prepare_frontier, TaskState.READY)
        with self.kernel.locked_task(
            task_id,
            expected_revision=current.revision,
            expected_state=TaskState.READY,
            expected_frontier=(prepare_frontier,),
            label="Effect",
            error_factory=self._kernel_error,
        ) as locked:
            descriptor = self.storage.read_task_descriptor(task_id)
            if descriptor is None:
                raise EffectLifecycleError("Effect Task has no TaskDescriptor")
            effect_object = self.storage.put_object(effect, kind="effect")
            request_object = self.storage.put_object(request, kind="executor-request")
            dispatch_object = self.storage.put_object(
                dispatch.to_dict(), kind="dispatch-envelope"
            )
            descriptor_object = self.storage.objects.inspect(
                self.storage.task_descriptor_object_digest(task_id)
            )
            projection = locked.commit(
                event_id=event_id
                or f"event:{self._token(task_id)}:dispatch:r{current.revision + 1}",
                kind=EventKind.EFFECT_DISPATCH_PREPARED,
                payload={
                    "descriptorDigest": descriptor.digest,
                    "descriptorObjectDigest": descriptor_object.digest,
                    "effectDigest": canonical_digest(effect),
                    "effectObjectDigest": effect_object.digest,
                    "requestDigest": canonical_digest(request),
                    "requestObjectDigest": request_object.digest,
                    "dispatchDigest": canonical_digest(dispatch.to_dict()),
                    "dispatchObjectDigest": dispatch_object.digest,
                    "reconcileFrontier": reconcile_frontier,
                    "verifyFrontier": verify_frontier,
                    "resultFrontier": result_frontier,
                },
                state=TaskState.WAITING,
                frontier=(reconcile_frontier,),
                referenced_objects=(
                    descriptor_object,
                    effect_object,
                    request_object,
                    dispatch_object,
                ),
            ).projection
            return PreparedDispatch(
                descriptor=descriptor,
                task_revision=projection.revision,
                effect_object=effect_object,
                request_object=request_object,
                dispatch_object=dispatch_object,
                effect=dict(effect),
                request=dict(request),
                dispatch=dispatch,
                reconcile_frontier=reconcile_frontier,
                verify_frontier=verify_frontier,
                result_frontier=result_frontier,
            )

    def load_prepared(self, task_id: str) -> PreparedDispatch:
        snapshot = self.storage.read_task_event(task_id)
        if snapshot.event_kind not in {
            EventKind.EFFECT_DISPATCH_PREPARED,
            EventKind.EFFECT_OUTCOME_UNKNOWN,
            EventKind.EFFECT_DISPATCH_OBSERVED,
        }:
            raise EffectLifecycleError("Task head does not preserve a prepared Dispatch")
        return self._prepared_from_snapshot(snapshot)

    def deliver(
        self,
        prepared: PreparedDispatch,
        executor: EffectExecutor,
    ) -> EffectStep:
        self._require_prepared_current(prepared)
        self._require_executor(prepared, executor)
        try:
            observation = executor.deliver(prepared.dispatch, prepared.request)
        except DeliveryUncertain as error:
            return self._record_unknown(prepared, error)
        return self._record_observation(prepared, observation, reconciled=False)

    def reconcile(
        self,
        task_id: str,
        executor: EffectExecutor,
    ) -> EffectStep:
        prepared = self.load_prepared(task_id)
        self._require_executor(prepared, executor)
        observation = executor.observe(prepared.dispatch, prepared.request)
        if observation is None:
            projection = self.storage.journal.get_task(task_id)
            if projection is None:
                raise KeyError(f"unknown Task: {task_id}")
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                reconciled=True,
            )
        return self._record_observation(prepared, observation, reconciled=True)

    def verify(
        self,
        task_id: str,
        verifier: Callable[[PreparedDispatch, ObservationEnvelope], VerificationReceipt],
        *,
        event_id: str | None = None,
    ) -> EffectStep:
        snapshot = self.storage.read_task_event(task_id)
        prepared = self._prepared_from_snapshot(snapshot)
        if snapshot.projection.state is not TaskState.VERIFYING:
            raise EffectLifecycleError("Effect verification requires a VERIFYING Task")
        if snapshot.projection.ready_frontier != (prepared.verify_frontier,):
            raise EffectLifecycleError("Task is not at the Effect verification frontier")
        data = self._data(snapshot)
        observation_digest = self._required_string(data, "observationDigest")
        observation_object_digest = self._required_string(
            data, "observationObjectDigest"
        )
        value = self.storage.objects.get(
            observation_object_digest, expected_kind="observation-envelope"
        )
        if not isinstance(value, dict):
            raise ObjectCorrupt("Observation envelope must be an object")
        try:
            observation = ObservationEnvelope.from_dict(value)
        except ValueError as error:
            raise ObjectCorrupt("Observation envelope is invalid") from error
        receipt = verifier(prepared, observation)
        if receipt.dispatch_id != prepared.dispatch.dispatch_id:
            raise EffectLifecycleError("Verification targets another Dispatch")
        if receipt.observation_digest != observation_digest:
            raise EffectLifecycleError("Verification targets another Observation")
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=TaskState.VERIFYING,
            expected_frontier=(prepared.verify_frontier,),
            label="Effect verification",
            error_factory=self._kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise EffectSuperseded("prepared Dispatch changed before verification")
            verification_object = self.storage.put_object(
                receipt.to_dict(), kind="verification-receipt"
            )
            verification_digest = canonical_digest(receipt.to_dict())
            references = self._state_references(data) + (
                self.storage.objects.inspect(observation_object_digest),
                verification_object,
            )
            projection = locked.commit(
                event_id=event_id
                or f"event:{self._token(task_id)}:verification:r{locked.projection.revision + 1}",
                kind=EventKind.VERIFICATION_RECORDED,
                payload={
                    **self._state_fields(data),
                    "observationDigest": observation_digest,
                    "observationObjectDigest": observation_object_digest,
                    "verificationDigest": verification_digest,
                    "verificationObjectDigest": verification_object.digest,
                },
                state=TaskState.READY if receipt.accepted else TaskState.BLOCKED,
                frontier=(prepared.result_frontier,),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                observation_digest=observation_digest,
                verification_digest=verification_digest,
            )

    def complete(
        self,
        task_id: str,
        outcome: TaskOutcome,
        *,
        event_id: str | None = None,
    ) -> EffectStep:
        snapshot = self.storage.read_task_event(task_id)
        prepared = self._prepared_from_snapshot(snapshot)
        if snapshot.projection.state not in {TaskState.READY, TaskState.BLOCKED}:
            raise EffectLifecycleError("Effect completion requires READY or BLOCKED")
        if snapshot.projection.ready_frontier != (prepared.result_frontier,):
            raise EffectLifecycleError("Task is not at the Effect result frontier")
        if (
            outcome.task_id != task_id
            or outcome.goal_id != prepared.descriptor.goal_id
        ):
            raise ValueError("TaskOutcome identity differs from TaskDescriptor")
        data = self._data(snapshot)
        retained_verification = data.get("verificationDigest")
        if outcome.verification_digest != retained_verification:
            raise ValueError("TaskOutcome verification differs from Task head")
        state = {
            "completed": TaskState.COMPLETED,
            "failed": TaskState.FAILED,
            "cancelled": TaskState.CANCELLED,
            "blocked": TaskState.BLOCKED,
        }[outcome.status]
        outcome_object = self.storage.put_object(outcome.to_dict(), kind="task-outcome")
        with self.kernel.locked_task(
            task_id,
            expected_revision=snapshot.projection.revision,
            expected_state=snapshot.projection.state,
            expected_frontier=(prepared.result_frontier,),
            label="Effect completion",
            error_factory=self._kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise EffectSuperseded("prepared Dispatch changed before completion")
            references = self._state_references(data)
            observation_digest = data.get("observationDigest")
            observation_object_digest = data.get("observationObjectDigest")
            verification_digest = data.get("verificationDigest")
            verification_object_digest = data.get("verificationObjectDigest")
            for digest in (observation_object_digest, verification_object_digest):
                if isinstance(digest, str):
                    references += (self.storage.objects.inspect(digest),)
            references += (outcome_object,)
            outcome_digest = canonical_digest(outcome.to_dict())
            projection = locked.commit(
                event_id=event_id
                or f"event:{self._token(task_id)}:outcome:r{locked.projection.revision + 1}",
                kind=EventKind.TASK_OUTCOME_RECORDED,
                payload={
                    **self._state_fields(data),
                    "observationDigest": observation_digest,
                    "observationObjectDigest": observation_object_digest,
                    "verificationDigest": verification_digest,
                    "verificationObjectDigest": verification_object_digest,
                    "outcomeDigest": outcome_digest,
                    "outcomeObjectDigest": outcome_object.digest,
                },
                state=state,
                frontier=(),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                observation_digest=(
                    observation_digest if isinstance(observation_digest, str) else None
                ),
                verification_digest=(
                    verification_digest if isinstance(verification_digest, str) else None
                ),
                outcome_digest=outcome_digest,
            )

    def _record_unknown(
        self,
        prepared: PreparedDispatch,
        error: DeliveryUncertain,
    ) -> EffectStep:
        with self.kernel.locked_task(
            prepared.descriptor.task_id,
            expected_revision=prepared.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.reconcile_frontier,),
            label="Effect",
            error_factory=self._kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise EffectSuperseded("prepared Dispatch identity changed")
            uncertainty: dict[str, JsonValue] = {
                "schemaVersion": 1,
                "kind": "ordivon.executor-uncertain-delivery",
                "dispatchId": prepared.dispatch.dispatch_id,
                "executorId": prepared.dispatch.executor_id,
                "errorType": type(error).__name__,
                "message": str(error)[:2_048],
            }
            uncertainty_object = self.storage.put_object(
                uncertainty, kind="executor-uncertain-delivery"
            )
            data = self._data(locked.snapshot)
            references = self._state_references(data) + (uncertainty_object,)
            projection = locked.commit(
                event_id=f"event:{self._token(prepared.descriptor.task_id)}:unknown:r{locked.projection.revision + 1}",
                kind=EventKind.EFFECT_OUTCOME_UNKNOWN,
                payload={
                    **self._state_fields(data),
                    "uncertaintyDigest": uncertainty_object.digest,
                },
                state=TaskState.WAITING,
                frontier=(prepared.reconcile_frontier,),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
            )

    def _record_observation(
        self,
        prepared: PreparedDispatch,
        observation: ObservationEnvelope,
        *,
        reconciled: bool,
    ) -> EffectStep:
        if (
            observation.dispatch_id != prepared.dispatch.dispatch_id
            or observation.executor_id != prepared.dispatch.executor_id
        ):
            raise EffectLifecycleError("Observation targets another Dispatch or executor")
        with self.kernel.locked_task(
            prepared.descriptor.task_id,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.reconcile_frontier,),
            label="Effect",
            error_factory=self._observation_kernel_error,
        ) as locked:
            current = self._prepared_from_snapshot(locked.snapshot)
            if current.dispatch != prepared.dispatch:
                raise EffectSuperseded("prepared Dispatch changed before observation")
            observation_value = observation.to_dict()
            observation_digest = canonical_digest(observation_value)
            observation_object = self.storage.put_object(
                observation_value, kind="observation-envelope"
            )
            if observation.status == "succeeded":
                state = TaskState.VERIFYING
                frontier = prepared.verify_frontier
            elif observation.status in {"failed", "rejected"}:
                state = TaskState.BLOCKED
                frontier = prepared.result_frontier
            else:
                state = TaskState.WAITING
                frontier = prepared.reconcile_frontier
            data = self._data(locked.snapshot)
            references = self._state_references(data) + (observation_object,)
            projection = locked.commit(
                event_id=f"event:{self._token(prepared.descriptor.task_id)}:observation:r{locked.projection.revision + 1}",
                kind=EventKind.EFFECT_DISPATCH_OBSERVED,
                payload={
                    **self._state_fields(data),
                    "observationDigest": observation_digest,
                    "observationObjectDigest": observation_object.digest,
                    "observationStatus": observation.status,
                    "reconciled": reconciled,
                },
                state=state,
                frontier=(frontier,),
                referenced_objects=references,
            ).projection
            return self._step(
                projection,
                dispatch_id=prepared.dispatch.dispatch_id,
                observation_digest=observation_digest,
                reconciled=reconciled,
            )

    def _prepared_from_snapshot(self, snapshot: TaskEventSnapshot) -> PreparedDispatch:
        data = self._data(snapshot)
        descriptor = self.storage.read_task_descriptor(snapshot.projection.task_id)
        if descriptor is None:
            raise EffectLifecycleError("Effect Task has no TaskDescriptor")
        effect_object = self.storage.objects.inspect(
            self._required_string(data, "effectObjectDigest")
        )
        request_object = self.storage.objects.inspect(
            self._required_string(data, "requestObjectDigest")
        )
        dispatch_object = self.storage.objects.inspect(
            self._required_string(data, "dispatchObjectDigest")
        )
        effect = self.storage.objects.get(effect_object.digest, expected_kind="effect")
        request = self.storage.objects.get(
            request_object.digest, expected_kind="executor-request"
        )
        dispatch_value = self.storage.objects.get(
            dispatch_object.digest, expected_kind="dispatch-envelope"
        )
        if not all(isinstance(item, dict) for item in (effect, request, dispatch_value)):
            raise ObjectCorrupt("prepared Effect objects must be objects")
        try:
            dispatch = DispatchEnvelope.from_dict(dispatch_value)
        except ValueError as error:
            raise ObjectCorrupt("prepared Dispatch is invalid") from error
        if (
            effect.get("effectId") != dispatch.effect_id
            or canonical_digest(effect) != self._required_string(data, "effectDigest")
            or canonical_digest(request) != dispatch.request_digest
            or canonical_digest(request) != self._required_string(data, "requestDigest")
            or canonical_digest(dispatch.to_dict())
            != self._required_string(data, "dispatchDigest")
        ):
            raise JournalCorruption("prepared Effect identities or semantic digests differ")
        return PreparedDispatch(
            descriptor=descriptor,
            task_revision=snapshot.projection.revision,
            effect_object=effect_object,
            request_object=request_object,
            dispatch_object=dispatch_object,
            effect=dict(effect),
            request=dict(request),
            dispatch=dispatch,
            reconcile_frontier=self._required_string(data, "reconcileFrontier"),
            verify_frontier=self._required_string(data, "verifyFrontier"),
            result_frontier=self._required_string(data, "resultFrontier"),
        )

    def _require_prepared_current(self, prepared: PreparedDispatch) -> None:
        self.kernel.current_snapshot(
            prepared.descriptor.task_id,
            expected_revision=prepared.task_revision,
            expected_state=TaskState.WAITING,
            expected_frontier=(prepared.reconcile_frontier,),
            label="Effect",
            error_factory=self._prepared_kernel_error,
        )

    @staticmethod
    def _require_executor(
        prepared: PreparedDispatch,
        executor: EffectExecutor,
    ) -> None:
        if executor.executor_id != prepared.dispatch.executor_id:
            raise ValueError("Dispatch targets another executor")

    def _require_frontier(
        self,
        task_id: str,
        frontier: str,
        state: TaskState,
    ) -> TaskProjection:
        return self.kernel.current_snapshot(
            task_id,
            expected_state=state,
            expected_frontier=(frontier,),
            label="Effect",
            error_factory=self._kernel_error,
        ).projection

    @staticmethod
    def _data(snapshot: TaskEventSnapshot) -> dict[str, JsonValue]:
        if not isinstance(snapshot.data, dict):
            raise JournalCorruption("Effect event data must be an object")
        return dict(snapshot.data)

    @staticmethod
    def _required_string(data: dict[str, JsonValue], field: str) -> str:
        value = data.get(field)
        if not isinstance(value, str):
            raise JournalCorruption(f"Effect event omitted {field}")
        return value

    @classmethod
    def _state_fields(cls, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            field: cls._required_string(data, field)
            for field in (
                "descriptorDigest",
                "descriptorObjectDigest",
                "effectDigest",
                "effectObjectDigest",
                "requestDigest",
                "requestObjectDigest",
                "dispatchDigest",
                "dispatchObjectDigest",
                "reconcileFrontier",
                "verifyFrontier",
                "resultFrontier",
            )
        }

    def _state_references(
        self,
        data: dict[str, JsonValue],
    ) -> tuple[StoredObject, ...]:
        return tuple(
            self.storage.objects.inspect(self._required_string(data, field))
            for field in (
                "descriptorObjectDigest",
                "effectObjectDigest",
                "requestObjectDigest",
                "dispatchObjectDigest",
            )
        )

    @staticmethod
    def _kernel_error(category: str, message: str) -> Exception:
        if category == "missing":
            return KeyError(message)
        if category == "revision":
            return EffectSuperseded(message)
        if category in {"state", "frontier"}:
            return EffectLifecycleError(message)
        return JournalCorruption(message)

    @staticmethod
    def _prepared_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision", "state", "frontier"}:
            return EffectSuperseded("prepared Dispatch is no longer current")
        return JournalCorruption(message)

    @staticmethod
    def _observation_kernel_error(category: str, message: str) -> Exception:
        if category in {"missing", "revision", "state", "frontier"}:
            return EffectSuperseded("Effect Task advanced before observation")
        return JournalCorruption(message)

    @staticmethod
    def _token(task_id: str) -> str:
        return task_id.removeprefix("task:")

    @staticmethod
    def _step(
        projection: TaskProjection,
        *,
        dispatch_id: str | None = None,
        observation_digest: str | None = None,
        verification_digest: str | None = None,
        outcome_digest: str | None = None,
        reconciled: bool = False,
    ) -> EffectStep:
        return EffectStep(
            task_id=projection.task_id,
            revision=projection.revision,
            state=projection.state,
            frontier=(projection.ready_frontier[0] if projection.ready_frontier else None),
            dispatch_id=dispatch_id,
            observation_digest=observation_digest,
            verification_digest=verification_digest,
            outcome_digest=outcome_digest,
            reconciled=reconciled,
        )
