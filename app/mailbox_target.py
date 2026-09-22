"""Keep the connecting Microsoft identity separate from the sending mailbox."""
from urllib.parse import quote


def graph_mailbox_path(actor: dict) -> str:
    target = actor.get("mailbox_user_id")
    return "/users/" + quote(target, safe="") if target else "/me"


def mailbox_key(actor: dict) -> str:
    return actor.get("mailbox_key") or actor["object_id"]