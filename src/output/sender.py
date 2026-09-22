"""CLI sending is disabled: Microsoft email needs a connected mailbox and draft approval."""
from config.settings import Settings
from src.models import EmailDraft


class EmailSender:
    def __init__(self, settings: Settings):
        self.s = settings

    def send_all(self, drafts: list[EmailDraft], delay_seconds: float = 20, *, approved: bool = False) -> list[str]:
        raise RuntimeError("Microsoft mailbox connection and draft approval are required. Send email from the web workspace.")
