from sqlalchemy import inspect, update
from sqlalchemy.sql.operators import eq, startswith_op

from sqlalchemy_auth_hooks.references import LiteralExpression, ReferenceCondition, ReferencedEntity
from tests.core.conftest import User


def test_update(engine, add_user, post_auth_handler, authorized_session):
    with authorized_session as session:
        session.execute(update(User).where(User.id == add_user.id).values(name="Jane"))
        session.commit()
    post_auth_handler.after_many_update.assert_called_once_with(
        authorized_session,
        ReferencedEntity(inspect(User), User.__table__),
        ReferenceCondition(
            left=User.__table__.c.id,
            operator=eq,
            right=LiteralExpression(add_user.id),
        ),
        {"name": "Jane"},
    )


async def test_async_update(async_engine, add_user_async, post_auth_handler, authorized_async_session):
    async with authorized_async_session as session:
        await session.execute(update(User).where(User.id == add_user_async.id).values(name="Jane"))
        await session.commit()
    post_auth_handler.after_many_update.assert_called_once_with(
        authorized_async_session.sync_session,
        ReferencedEntity(inspect(User), User.__table__),
        ReferenceCondition(
            left=User.__table__.c.id,
            operator=eq,
            right=LiteralExpression(add_user_async.id),
        ),
        {"name": "Jane"},
    )


def test_update_all(engine, post_auth_handler, add_user, authorized_session):
    with authorized_session as session:
        session.execute(update(User).values(name="John", age=10))
        session.commit()
    post_auth_handler.after_many_update.assert_called_once_with(
        authorized_session,
        ReferencedEntity(entity=inspect(User), selectable=User.__table__),
        None,
        {"name": "John", "age": 10},
    )


def test_update_condition(engine, post_auth_handler, add_user, authorized_session):
    with authorized_session as session:
        session.execute(update(User).where(User.name.startswith("J")).values(name="John", age=10))
        session.commit()
    selectable = User.__table__
    post_auth_handler.after_many_update.assert_called_once_with(
        authorized_session,
        ReferencedEntity(
            entity=inspect(User),
            selectable=selectable,
        ),
        ReferenceCondition(
            left=selectable.c.name,
            operator=startswith_op,
            right=LiteralExpression("J"),
        ),
        {"name": "John", "age": 10},
    )
