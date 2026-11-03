"""Route one inbound message to Thread A (Owner, trusted) or Thread B (Debtor, untrusted)."""
from src.ingest.debtor_flow import handle_debtor
from src.ingest.owner_flow import handle_owner
from src.ingest.payload import InboundMessage


async def dispatch(app, msg: InboundMessage) -> None:
    tenant_id = app.state.settings.owner_tenants.get(msg.sender)
    if tenant_id:
        await handle_owner(app, tenant_id, msg)
    else:
        await handle_debtor(app, msg)
