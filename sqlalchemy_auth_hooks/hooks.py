import asyncio
from collections import defaultdict
from functools import partial
from threading import Thread
from typing import Any, Callable, Coroutine, TypeVar, cast

import structlog
from sqlalchemy import (
    BindParameter,
    Column,
    Update,
    event,
    inspect,
)
from sqlalchemy.orm import (
    InstanceState,
    ORMExecuteState,
    Session,
    UOWTransaction,
)
from structlog.stdlib import BoundLogger

from sqlalchemy_auth_hooks.auth_handler import AuthHandler
from sqlalchemy_auth_hooks.authorization import StatementAuthorizer
from sqlalchemy_auth_hooks.events import CreateSingleEvent, DeleteSingleEvent, Event, UpdateManyEvent, UpdateSingleEvent
from sqlalchemy_auth_hooks.post_auth_handler import PostAuthHandler
from sqlalchemy_auth_hooks.session import check_skip
from sqlalchemy_auth_hooks.utils import extract_references, run_loop

logger: BoundLogger = structlog.get_logger()

T = TypeVar("T")


class SQLAlchemyAuthHooks:
    def __init__(self, auth_handler: AuthHandler, post_auth_handler: PostAuthHandler) -> None:
        self.auth_handler = auth_handler
        self.post_auth_handler = post_auth_handler
        self._pending_events: dict[Session, dict[tuple[Any, ...] | None, list[Event[Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._loop = asyncio.new_event_loop()
        self._executor_thread = Thread(target=partial(run_loop, self._loop), daemon=True)
        self._executor_thread.start()
        self._authorizer = StatementAuthorizer(self.auth_handler)

    def call_async(self, func: Callable[..., Coroutine[T, None, Any]], *args: Any) -> T:
        future = asyncio.run_coroutine_threadsafe(func(*args), self._loop)
        return future.result()

    @staticmethod
    def _get_state_changes(state: InstanceState[Any]) -> dict[str, Any]:
        return {attr.key: attr.history.added[-1] for attr in state.attrs if attr.history.has_changes()}  # type: ignore

    def after_flush(self, session: Session, flush_context: UOWTransaction) -> None:
        logger.debug("after_flush")
        if check_skip(session):
            return
        for _mapper, states in flush_context.mappers.items():
            for state in states:
                if state.modified and state.has_identity and state.is_instance:
                    # Update
                    changes = self._get_state_changes(state)
                    self._pending_events[session][state.identity_key].append(UpdateSingleEvent(state, changes))

    def before_flush(
        self, session: Session, flush_context: UOWTransaction, instances: list[InstanceState[Any]] | None
    ) -> None:
        logger.debug("before_flush")
        if check_skip(session):
            return
        pending_updates = []
        for instance in session.dirty:
            state = inspect(instance)
            if state.modified and state.has_identity and state.is_instance:
                # Update
                changes = self._get_state_changes(state)
                pending_updates.append((state, changes))
        if pending_updates:
            self.call_async(self._authorizer.authorize_object_update, session, pending_updates)

    def after_flush_postexec(self, session: Session, flush_context: UOWTransaction) -> None:
        logger.debug("after_flush_postexec")
        if check_skip(session):
            return
        for _mapper, states in flush_context.mappers.items():
            for state in states:
                if not state.deleted and not state.detached and state.has_identity and state.is_instance:
                    # Create
                    self._pending_events[session][state.identity_key].append(CreateSingleEvent(state))
                elif state.deleted and state.has_identity and state.is_instance:
                    # Delete
                    self._pending_events[session][state.identity_key].append(DeleteSingleEvent(state))

    def after_commit(self, session: Session) -> None:
        logger.debug("after_commit")
        if check_skip(session):
            return
        if session not in self._pending_events:
            logger.debug("No tracked session states to process")
            return
        for pending_instance_key in self._pending_events[session]:
            for hook in self._pending_events[session][pending_instance_key]:
                self.call_async(hook.trigger, session, self.post_auth_handler)
        del self._pending_events[session]

    def after_rollback(self, session: Session) -> None:
        logger.debug("after_rollback")
        if check_skip(session):
            return
        if session not in self._pending_events:
            logger.debug("No tracked session states to process")
            return
        del self._pending_events[session]

    def handle_update(self, orm_execute_state: ORMExecuteState) -> None:
        statement = cast(Update, orm_execute_state.statement)
        if "entity" not in statement.entity_description:
            # ORM update
            return
        conditions, references = extract_references(statement)

        parameters = cast(dict[Column[Any], BindParameter[Any]], statement._values)  # type: ignore
        updated_data: dict[str, Any] = {col.name: parameter.value for col, parameter in parameters.items()}
        for mapped_dict in references.values():
            for referenced_entity in mapped_dict.values():
                self._pending_events[orm_execute_state.session][None].append(
                    UpdateManyEvent(referenced_entity, conditions, updated_data)
                )

    def do_orm_execute(self, orm_execute_state: ORMExecuteState) -> None:
        logger.debug("do_orm_execute")
        if check_skip(orm_execute_state.session):
            return
        if orm_execute_state.is_select:
            self.call_async(self._authorizer.authorize_select, orm_execute_state)
        elif orm_execute_state.is_update:
            self.call_async(self._authorizer.authorize_update, orm_execute_state)
            self.handle_update(orm_execute_state)


def register_hooks(handler: AuthHandler, post_auth_handler: PostAuthHandler) -> None:
    """
    Register hooks for SQLAlchemy ORM events.
    """

    hooks = SQLAlchemyAuthHooks(handler, post_auth_handler)
    event.listen(Session, "after_flush", hooks.after_flush)
    event.listen(Session, "before_flush", hooks.before_flush)
    event.listen(Session, "after_flush_postexec", hooks.after_flush_postexec)
    event.listen(Session, "after_commit", hooks.after_commit)
    event.listen(Session, "after_rollback", hooks.after_rollback)
    event.listen(Session, "do_orm_execute", hooks.do_orm_execute)