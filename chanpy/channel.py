import asyncio
import contextlib
import threading
from collections import deque
from . import handlers as hd
from . import xf


@contextlib.contextmanager
def acquire_handlers(*handlers):
    # Consistent lock acquisition order
    for h in sorted(handlers, key=lambda h: h.lock_id):
        is_acquired = h.acquire()
        assert is_acquired

    try:
        yield True
    finally:
        for h in handlers:
            h.release()


MAX_QUEUE_SIZE = 1024


class MaxQueueSize(Exception):
    """Maximum pending operations exceeded"""


def nop_ex_handler(e):
    raise e


class Chan:
    def __init__(self, buf=None, xform=None, ex_handler=None):
        xform = xf.identity if xform is None else xform
        ex_handler = nop_ex_handler if ex_handler is None else ex_handler
        self._buf = buf
        self._takes = deque()
        self._puts = deque()
        self._is_closed = False
        self._xform_is_completed = False
        self._lock = threading.Lock()

        def ex_handler_xform(rf):
            def wrapper(*args, **kwargs):
                try:
                    return rf(*args, **kwargs)
                except Exception as e:
                    val = ex_handler(e)
                    if val is not None:
                        self._buf.put(val)
            return wrapper

        def step(_, val):
            if val is None:
                raise TypeError('xform cannot produce None')
            self._buf.put(val)

        rf = xf.multi_arity(lambda: None, lambda _: None, step)
        self._buf_rf = ex_handler_xform(xform(rf))

    def a_put(self, val, *, wait=True):
        flag = hd.create_flag()
        future = hd.FlagFuture(flag)
        handler = hd.FlagHandler(flag, hd.future_deliver_fn(future), wait)
        ret = self._put(handler, val)
        if ret is not None:
            asyncio.Future.set_result(future, ret[0])
        return future

    def a_get(self, *, wait=True):
        flag = hd.create_flag()
        future = hd.FlagFuture(flag)
        handler = hd.FlagHandler(flag, hd.future_deliver_fn(future), wait)
        ret = self._get(handler)
        if ret is not None:
            asyncio.Future.set_result(future, ret[0])
        return future

    def t_put(self, val, *, wait=True):
        prom = hd.Promise()
        ret = self._put(hd.FnHandler(prom.deliver, wait), val)
        if ret is not None:
            return ret[0]
        return prom.deref()

    def t_get(self, *, wait=True):
        prom = hd.Promise()
        ret = self._get(hd.FnHandler(prom.deliver, wait))
        if ret is not None:
            return ret[0]
        return prom.deref()

    def offer(self, val):
        return self.t_put(val, wait=False)

    def poll(self):
        return self.t_get(wait=False)

    def close(self):
        with self._lock:
            self._cleanup()
            self._close()

    def _put(self, handler, val):
        if val is None:
            raise TypeError('item cannot be None')
        with self._lock:
            self._cleanup()

            if self._is_closed:
                return self._fail_op(handler, False)

            # Attempt to transfer val onto buf
            if self._buf is not None and not self._buf.is_full():
                with handler:
                    if not handler.is_active:
                        return False
                    handler.commit()

                self._buf_put(val)
                self._distribute_buf_vals()
                return True,

            # Attempt to transfer val to a taker
            if self._buf is None:
                while len(self._takes) > 0:
                    taker = self._takes.popleft()
                    with acquire_handlers(handler, taker):
                        if handler.is_active and taker.is_active:
                            handler.commit()
                            taker.commit()(val)
                            return True,

            if not handler.is_blockable:
                return self._fail_op(handler, False)

            # Enqueue
            if len(self._puts) >= MAX_QUEUE_SIZE:
                raise MaxQueueSize
            self._puts.append((handler, val))

    def _get(self, handler):
        with self._lock:
            self._cleanup()

            # Attempt to take val from buf
            if self._buf is not None and len(self._buf) > 0:
                with handler:
                    if not handler.is_active:
                        return None,
                    handler.commit()

                ret = self._buf.get()

                # Transfer vals from putters onto buf
                while len(self._puts) > 0 and not self._buf.is_full():
                    putter, val = self._puts.popleft()
                    with putter:
                        if putter.is_active:
                            putter.commit()(True)
                            self._buf_put(val)

                self._complete_xform_if_ready()
                return ret,

            # Attempt to take val from a putter
            if self._buf is None:
                while len(self._puts) > 0:
                    putter, val = self._puts.popleft()
                    with acquire_handlers(handler, putter):
                        if handler.is_active and putter.is_active:
                            handler.commit()
                            putter.commit()(True)
                            return val,

            if self._is_closed or not handler.is_blockable:
                return self._fail_op(handler, None)

            # Enqueue
            if len(self._takes) >= MAX_QUEUE_SIZE:
                raise MaxQueueSize
            self._takes.append(handler)

    def _cleanup(self):
        self._takes = deque(h for h in self._takes if h.is_active)
        self._puts = deque((h, v) for h, v in self._puts if h.is_active)

    @staticmethod
    def _fail_op(handler, val):
        with handler:
            if handler.is_active:
                handler.commit()
                return val,

    def _buf_put(self, val):
        if xf.is_reduced(self._buf_rf(None, val)):
            # If reduced value is returned then no more input is allowed onto
            # buf. To ensure this, remove all pending puts and close ch.
            for putter, _ in self._puts:
                with putter:
                    if putter.is_active:
                        putter.commit()(False)
            self._puts.clear()
            self._close()

    def _distribute_buf_vals(self):
        while len(self._takes) > 0 and len(self._buf) > 0:
            taker = self._takes.popleft()
            with taker:
                if taker.is_active:
                    taker.commit()(self._buf.get())

    def _complete_xform_if_ready(self):
        """Calls the xform completion arity exactly once iff all input has been
        placed onto buf"""
        if (self._is_closed and
                len(self._puts) == 0 and
                not self._xform_is_completed):
            self._xform_is_completed = True
            self._buf_rf(None)

    def _close(self):
        self._is_closed = True

        if self._buf is not None:
            self._complete_xform_if_ready()
            self._distribute_buf_vals()

        # Remove pending takes
        # No-op if there are pending puts or buffer isn't empty
        for taker in self._takes:
            with taker:
                if taker.is_active:
                    taker.commit()(None)
        self._takes.clear()

    async def __aiter__(self):
        while True:
            value = await self.a_get()
            if value is None:
                break
            yield value
