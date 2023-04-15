import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from sqlalchemy_auth_hooks.handler import ReferencedEntity
from tests.conftest import Group, User, UserGroup


@pytest.fixture
@pytest.mark.early
def add_user(engine):
    user = User(name="John", age=42)
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
    return user


@pytest.fixture
@pytest.mark.early
def user_group(engine, add_user):
    with Session(engine) as session:
        group = Group(name="Test Users")
        session.add(group)
        session.flush()
        group.users.append(UserGroup(user_id=add_user.id, group_id=group.id))
        session.commit()
        session.refresh(group)
        session.expunge(group)
    return group


@pytest.fixture(autouse=True)
def cleanup(engine):
    yield
    with Session(engine) as session:
        session.execute(delete(User).where())
        session.execute(delete(Group).where())
        session.execute(delete(UserGroup).where())
        session.commit()


@pytest.fixture(autouse=True)
def reset_handler(auth_handler):
    auth_handler.reset_mock()


def test_simple_get(engine, add_user, auth_handler):
    with Session(engine) as session:
        session.get(User, add_user.id)
    mapper = inspect(User)
    auth_handler.on_select.assert_called_once_with(
        [ReferencedEntity(entity=mapper, keys={"id": add_user.id}, selectable=User.__table__)]
    )


def test_simple_select(engine, add_user, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).filter_by(id=add_user.id))
    mapper = inspect(User)
    auth_handler.on_select.assert_called_once_with(
        [ReferencedEntity(entity=mapper, keys={"id": add_user.id}, selectable=User.__table__)]
    )


def test_simple_select_where(engine, add_user, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).where(User.id == add_user.id))
    mapper = inspect(User)
    auth_handler.on_select.assert_called_once_with(
        [ReferencedEntity(entity=mapper, keys={"id": add_user.id}, selectable=User.__table__)]
    )


def test_simple_select_column_only(engine, add_user, auth_handler):
    with Session(engine) as session:
        session.execute(select(User.name).filter_by(id=add_user.id))
    mapper = inspect(User)
    auth_handler.on_select.assert_called_once_with(
        [ReferencedEntity(entity=mapper, keys={"id": add_user.id}, selectable=User.__table__)]
    )


def test_select_multiple_pk(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(UserGroup).filter_by(user_id=add_user.id, group_id=user_group.id))
    mapper = inspect(UserGroup)
    auth_handler.on_select.assert_called_once_with(
        [
            ReferencedEntity(
                entity=mapper, keys={"user_id": add_user.id, "group_id": user_group.id}, selectable=UserGroup.__table__
            )
        ]
    )


def test_join(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).join(User.groups).join(UserGroup.group))
    auth_handler.on_select.assert_called_once_with(
        [
            ReferencedEntity(entity=inspect(User), selectable=User.__table__),
            ReferencedEntity(entity=inspect(UserGroup), selectable=UserGroup.__table__),
            ReferencedEntity(entity=inspect(Group), selectable=Group.__table__),
        ]
    )


def test_join_with_where(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).join(User.groups).join(UserGroup.group).where(Group.id == 1))
    auth_handler.on_select.assert_called_once_with(
        [
            ReferencedEntity(entity=inspect(User), selectable=User.__table__),
            ReferencedEntity(entity=inspect(UserGroup), selectable=UserGroup.__table__),
            ReferencedEntity(entity=inspect(Group), keys={"id": 1}, selectable=Group.__table__),
        ]
    )


def test_join_with_condition(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).join(User.groups).join(UserGroup.group.and_(Group.id == 1)))
    # Although the condition is on the join, treat it as without a condition for now
    auth_handler.on_select.assert_called_once_with(
        [
            ReferencedEntity(entity=inspect(User), selectable=User.__table__),
            ReferencedEntity(entity=inspect(UserGroup), selectable=UserGroup.__table__),
            ReferencedEntity(entity=inspect(Group), selectable=Group.__table__),
        ]
    )


def test_join_recursive(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.add(user_group)
        group = Group(name="Test Users 2")
        user_group.subgroups.append(group)
        session.add(group)
        session.commit()
        alias = aliased(Group)
        session.execute(select(Group).join(Group.subgroups.of_type(alias)))
    # Called when adding the subgroup
    auth_handler.on_select.assert_any_call([ReferencedEntity(entity=inspect(Group), selectable=Group.__table__)])
    # Select itself
    auth_handler.on_select.assert_called_with(
        [
            ReferencedEntity(entity=inspect(Group), selectable=Group.__table__),
            ReferencedEntity(entity=inspect(Group), selectable=alias),
        ]
    )


def test_join_recursive_with_condition(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.add(user_group)
        group = Group(name="Test Users 2")
        user_group.subgroups.append(group)
        session.add(group)
        session.commit()
        alias = aliased(Group)
        session.execute(select(Group).join(Group.subgroups.of_type(alias)).where(alias.id == 2))
    # Called when adding the subgroup
    auth_handler.on_select.assert_any_call([ReferencedEntity(entity=inspect(Group), selectable=Group.__table__)])
    # The select itself
    auth_handler.on_select.assert_called_with(
        [
            ReferencedEntity(entity=inspect(Group), selectable=Group.__table__),
            ReferencedEntity(entity=inspect(Group), selectable=alias, keys={"id": 2}),
        ]
    )


def test_join_selectinload(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).options(selectinload(User.groups).selectinload(UserGroup.group))).all()
    # First, the user table is queried
    auth_handler.on_select.assert_any_call([ReferencedEntity(entity=inspect(User), selectable=User.__table__)])
    # Then, a selectinload is performed on the user-groups
    auth_handler.on_select.assert_any_call(
        [ReferencedEntity(entity=inspect(UserGroup), selectable=UserGroup.__table__)]
    )
    # Finally, a selectinload is performed on the groups as well
    auth_handler.on_select.assert_called_with([ReferencedEntity(entity=inspect(Group), selectable=Group.__table__)])


def test_join_joinedload(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        session.execute(select(User).options(joinedload(User.groups).joinedload(UserGroup.group))).unique().all()
    # All should be queried at once
    auth_handler.on_select.assert_called_once()
    assert len(auth_handler.on_select.call_args_list[0].args[0]) == 3
    assert auth_handler.on_select.call_args_list[0].args[0][0] == ReferencedEntity(
        entity=inspect(User), selectable=User.__table__
    )
    # Joinedloads have anonymous aliases by default so it's hard to compare them
    assert auth_handler.on_select.call_args_list[0].args[0][1].entity == inspect(UserGroup)
    assert auth_handler.on_select.call_args_list[0].args[0][1].selectable.original == UserGroup.__table__
    assert auth_handler.on_select.call_args_list[0].args[0][2].entity == inspect(Group)
    assert auth_handler.on_select.call_args_list[0].args[0][2].selectable.original == Group.__table__


def test_join_lazyload(engine, add_user, user_group, auth_handler):
    with Session(engine) as session:
        user: User = session.scalar(select(User).limit(1))
        _ = user.groups
        _ = user.groups[0].group.name
    assert auth_handler.on_select.call_count == 3
    auth_handler.on_select.assert_any_call([ReferencedEntity(entity=inspect(User), selectable=User.__table__)])
    auth_handler.on_select.assert_any_call(
        [ReferencedEntity(entity=inspect(UserGroup), selectable=UserGroup.__table__)]
    )
    # Only one group should be queried
    auth_handler.on_select.assert_called_with(
        [ReferencedEntity(entity=inspect(Group), selectable=Group.__table__, keys={"id": 1})]
    )
