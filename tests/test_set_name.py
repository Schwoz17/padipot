from app.channels.whatsapp import flows


def test_set_name_updates_member_name(db_session, make_member):
    member = make_member("2340000601", "aether.silver")

    reply = flows.handle_set_name(db_session, member=member, raw_args=" Chidi Okafor")

    assert "aether.silver" in reply
    assert "Chidi Okafor" in reply
    assert member.name == "Chidi Okafor"


def test_set_name_rejects_empty_input(db_session, make_member):
    member = make_member("2340000602", "aether.silver")

    reply = flows.handle_set_name(db_session, member=member, raw_args="   ")

    assert "SET NAME <your full name>" in reply
    assert member.name == "aether.silver"  # unchanged
