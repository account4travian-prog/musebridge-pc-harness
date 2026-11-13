"""Explicit, bounded continuation of a user's goal in a dedicated Muse side chat."""
from __future__ import annotations

import asyncio
import math
import threading
import time
import json

from musegadget.muse_turn import MuseTurn, ReplayScope, TaskFinished


_MAX_ROUNDS = 20
_MAX_TURN_TIMEOUT = 3600
_MAX_REASON = 240


class GoalRunner:
    """Run explicit continuation rounds while following Muse's acknowledged turns.

    No work starts at construction or startup. Call :meth:`run` explicitly after
    the owner chooses Continue Goal. The caller supplies the ordinary chat send
    path; this class has no model, network, or local-agent dependency of its own.
    """

    def __init__(self, catalog, send, status):
        self.catalog = catalog
        self.send = send
        self.status = status
        self._guard = threading.RLock()
        self._active = False
        self._active_cid = None
        self._turn = None
        self._wake = None
        self._loop = None
        self._task_finished = False
        self._turn_error = None
        self._scopes = {}
        self._stop_event = None
        self._run_task = None

    async def run(self, cid, max_rounds=5, turn_timeout_s=300):
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= _MAX_ROUNDS:
            raise ValueError(f"max_rounds must be an integer from 1 to {_MAX_ROUNDS}")
        if isinstance(turn_timeout_s, bool) or not isinstance(turn_timeout_s, (int, float)):
            raise ValueError("turn_timeout_s must be a number")
        turn_timeout_s = float(turn_timeout_s)
        if not math.isfinite(turn_timeout_s) or not 0 < turn_timeout_s <= _MAX_TURN_TIMEOUT:
            raise ValueError(f"turn_timeout_s must be in (0, {_MAX_TURN_TIMEOUT}]")
        with self._guard:
            if self._active:
                raise RuntimeError("a goal continuation is already running")
            self._active = True
            self._active_cid = cid
            self._loop = asyncio.get_running_loop()
            self._wake = asyncio.Event()
            self._stop_event = asyncio.Event()
            self._run_task = asyncio.current_task()
        rounds = 0
        try:
            context = self.catalog.context(cid)
            conversation = self.catalog.conversation(cid)
            goal = context.get("goal")
            remote_id = conversation.get("remote_id")
            if not isinstance(goal, str) or not goal.strip():
                raise ValueError("Set a nonempty goal before continuing")
            if not isinstance(remote_id, str) or not remote_id.strip():
                raise ValueError("Goal continuation requires a dedicated Muse side chat")
            if context.get("goal_state") != "active":
                raise ValueError("Set the goal state to active before continuing")
            self._scopes.setdefault(cid, ReplayScope())

            for round_number in range(1, max_rounds + 1):
                self.round_number = round_number
                context = self.catalog.context(cid)
                if context.get("goal_state") != "active":
                    self._say(cid, "Goal continuation stopped because the goal is " + str(context.get("goal_state")) + ".")
                    return self._result(cid, rounds)
                if self._stop_event.is_set():
                    await self._pause(cid, "Goal continuation was stopped by the owner.")
                    return self._result(cid, rounds)

                conversation = self.catalog.conversation(cid)
                remote_id = conversation.get("remote_id")
                if not isinstance(remote_id, str) or not remote_id.strip():
                    await self._pause(cid, "The dedicated Muse chat is unavailable.")
                    return self._result(cid, rounds)

                turn = MuseTurn(remote_id, owns_chat=True, replay_scope=self._scopes[cid])
                with self._guard:
                    self._turn = turn
                    self._task_finished = False
                    self._turn_error = None
                    self._wake.clear()
                self._say(cid, f"Continuing goal — round {round_number} of {max_rounds}.")
                prompt = self._prompt(context, round_number, max_rounds)
                deadline = time.monotonic() + turn_timeout_s
                try:
                    ack = await asyncio.wait_for(self.send(cid, prompt), timeout=max(0.001, deadline-time.monotonic()))
                    if ack.get('_bridge_tracking_ok') is False:
                        self._retire_active_turn(turn)
                        await self._pause(cid,'Message acknowledged, but local tracking needs inspection. No continuation or retry was sent.')
                        return self._result(cid,rounds)
                    # A send exception or malformed acknowledgement is ambiguous:
                    # never issue the same round again automatically.
                    held = turn.acknowledge(ack)
                    with self._guard:
                        if any(isinstance(item, TaskFinished) for item in held):
                            self._task_finished = True
                except Exception as exc:
                    self._retire_active_turn(turn)
                    await self._pause(cid, "Muse send was not confirmed (" + type(exc).__name__ + "). No retry was sent.")
                    return self._result(cid, rounds)

                done, reason = await self._await_turn(cid, turn, max(0.0, deadline-time.monotonic()))
                self._retire_active_turn(turn)
                if not done:
                    context = self.catalog.context(cid)
                    if context.get("goal_state") != "active":
                        self._say(cid, "Goal continuation stopped because the goal is " + str(context.get("goal_state")) + ".")
                        return self._result(cid, rounds)
                    await self._pause(cid, reason or "Muse did not confirm completion before the deadline.")
                    return self._result(cid, rounds)

                rounds += 1
                context = self.catalog.context(cid)
                state = context.get("goal_state")
                if state != "active":
                    self._say(cid, "Goal continuation stopped because the goal is " + str(state) + ".")
                    return self._result(cid, rounds)
                if self._stop_event.is_set():
                    await self._pause(cid, "Goal continuation was stopped by the owner.")
                    return self._result(cid, rounds)

            context = self.catalog.context(cid)
            if context.get("goal_state") == "active":
                await self._pause(cid, f"Paused after the {max_rounds}-round continuation limit. Continue Goal to resume.")
            return self._result(cid, rounds)
        except asyncio.CancelledError:
            turn = self._turn
            if turn:
                self._retire_active_turn(turn)
            await self._pause(cid, "Goal continuation was cancelled; the send outcome may be unknown.")
            raise
        except Exception as exc:
            await self._pause(cid, str(exc))
            return self._result(cid, rounds)
        finally:
            with self._guard:
                self._turn = None
                self._active_cid = None
                self._active = False
                self._run_task = None
                self._loop = None
                self._wake = None
                self._stop_event = None

    def feed(self, cid, event):
        """Feed one raw chat event; only the active conversation's turn can advance."""
        if not isinstance(event, dict):
            return []
        with self._guard:
            scope = self._scopes.get(cid)
            if cid != self._active_cid or self._turn is None:
                if scope:
                    try:
                        remote_id = self.catalog.conversation(cid).get("remote_id")
                        scope.observe_idle(event, remote_id)
                    except (ValueError, TypeError):
                        pass
                return []
            if event.get("type") == "disconnect" or event.get("event") == "connection.closed":
                self._turn_error = "Muse disconnected during goal continuation."
                self._wake_threadsafe()
                return []
            try:
                updates = self._turn.feed(event)
                if any(isinstance(item, TaskFinished) for item in updates):
                    self._task_finished = True
                self._wake_threadsafe()
                return updates
            except Exception as exc:
                self._turn_error = "Muse event could not be reconciled (" + type(exc).__name__ + ")."
                self._wake_threadsafe()
                return []

    def disconnect(self, cid, reason="Muse disconnected during goal continuation."):
        """Notify the active run of a lost/closed subscription without retrying it."""
        with self._guard:
            if cid == self._active_cid and self._turn is not None:
                self._turn_error = self._bounded(reason)
                self._wake_threadsafe()

    async def stop(self):
        """Cancel the active continuation and pause its goal; safe when idle."""
        with self._guard:
            task = self._run_task
            if not task:
                return
            if self._stop_event:
                self._stop_event.set()
            self._wake_threadsafe()
            if task is asyncio.current_task():
                return
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _await_turn(self, cid, turn, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            with self._guard:
                if self._turn_error:
                    return False, self._turn_error
                if self._stop_event and self._stop_event.is_set():
                    return False, "Goal continuation was stopped by the owner."
                task_finished = self._task_finished
                bound_reply=any(reply.bound and reply.done for reply in turn.replies.values())
                if (task_finished or bound_reply) and turn.complete(time.monotonic()):
                    return True, None
                wake = self._wake
            context = self.catalog.context(cid)
            if context.get("goal_state") != "active":
                return False, None  # The caller observes the owner's/tool's new state.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, "Muse did not confirm task completion before the deadline."
            # Polling is bounded and also observes goal state changes made by tools.
            try:
                await asyncio.wait_for(wake.wait(), timeout=min(0.25, remaining))
            except asyncio.TimeoutError:
                pass
            with self._guard:
                if self._wake is wake:
                    wake.clear()

    def _wake_threadsafe(self):
        if self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)

    def _retire_active_turn(self, turn):
        # Keep event ingestion and replay retirement atomic; a late feed may not
        # slip between the turn snapshot and clearing the active lineage.
        with self._guard:
            turn.retire()
            if self._turn is turn:
                self._turn = None

    async def _pause(self, cid, reason):
        reason = self._bounded(reason)
        try:
            context = self.catalog.context(cid)
            if context.get("goal_state") == "active":
                self.catalog.plan(cid, state="paused")
                self.catalog.event(cid, "goal.paused", {"reason": reason})
        except Exception:
            pass
        self._say(cid, reason)

    def _say(self, cid, message):
        try:
            self.status(cid, self._bounded(message))
        except Exception:
            pass

    def _result(self, cid, rounds):
        try:
            state = self.catalog.context(cid).get("goal_state")
        except Exception:
            state = "unknown"
        return {"conversation_id": cid, "rounds": rounds, "goal_state": state}

    @staticmethod
    def _bounded(reason):
        text = str(reason).replace("\r", " ").replace("\n", " ").strip()
        return text[:_MAX_REASON] or "Goal continuation paused."

    @staticmethod
    def _prompt(context, round_number, max_rounds):
        plan = context.get("plan") or "(no plan recorded)"
        goal = context.get("goal") or ""
        return (
            "Continue the user's explicitly active goal in this conversation.\n"
            f"Goal: {goal}\nCurrent plan: {plan}\n"
            f"Structured tasks: {json.dumps(context.get('tasks',[]),ensure_ascii=False)}\n"
            f"This is continuation round {round_number} of {max_rounds}.\n"
            "Take one bounded, verifiable next step. Use the available MuseBridge tools, "
            "verify consequential results, and update the plan with what changed and what remains. "
            "When structured tasks exist, inspect task.list and complete their checks/dependencies before declaring the goal complete. "
            "If the goal is finished, set its state to complete. If it cannot proceed, set blocked; "
            "if owner attention is needed, set paused. Otherwise keep it active and finish this turn "
            "with a concise progress report. Do not claim completion without checking the result."
        )
