"""Focused integration regressions found during read-only harness review."""
import asyncio

from musebridge.catalog import Catalog
from musebridge.core import Core


class PendingSession:
    def __init__(self):
        self.chat_subscribed = asyncio.Event()
        self.chat_subscribed.set()
        self.started = asyncio.Event()
        self.wait_for_ack = asyncio.Event()

    async def send_chat(self, _text, session_id=None):
        self.started.set()
        await self.wait_for_ack.wait()
        return {"ok": True, "response": {}}


def test_cancelled_send_is_immediately_marked_unknown(tmp_path):
    async def scenario(catalog, cid):
        core = Core(None, lambda _: None, lambda *_: None, lambda _: None, catalog=catalog)
        core.conversation_id = cid
        core.session = PendingSession()
        task = asyncio.create_task(core.send("work whose acknowledgement is pending"))
        await core.session.started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    catalog = Catalog(tmp_path / "catalog")
    project = catalog.create_project("Review", str(tmp_path))
    cid = catalog.create_conversation("Pending send", project["id"])["id"]
    try:
        asyncio.run(scenario(catalog, cid))
        rows = catalog.rows("SELECT state FROM outbox WHERE conversation_id=?", (cid,))
        assert len(rows) == 1
        assert rows[0]["state"] == "unknown"
    finally:
        catalog.close()
