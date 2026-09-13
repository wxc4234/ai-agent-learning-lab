from sqlalchemy.orm import Session

from app.repositories.user_repository import get_or_create_user


def test_get_or_create_user_reuses_existing_user(engine):
    with Session(engine) as session:
        first_user = get_or_create_user(session, "test-user")
        second_user = get_or_create_user(session, "test-user")

        assert first_user.id == second_user.id
        assert second_user.external_id == "test-user"
