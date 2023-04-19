from tests.core.conftest import User


def test_create_session(engine, auth_handler, authorized_session):
    user = User(name="John", age=42)
    with authorized_session as session:
        session.add(user)
        session.commit()
        assert user.id is not None
        auth_handler.after_single_create.assert_called_once_with(authorized_session, user)


def test_create_rollback(engine, auth_handler, authorized_session):
    user = User(name="John", age=42)
    with authorized_session as session:
        session.add(user)
        session.rollback()
        assert user.id is None
    auth_handler.after_single_create.assert_not_called()


def test_create_rollback_implicit(engine, auth_handler, authorized_session):
    user = User(name="John", age=42)
    with authorized_session as session:
        session.add(user)
        assert user.id is None
    auth_handler.after_single_create.assert_not_called()