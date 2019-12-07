import itertools
import functools
import random
from collections import deque


class _UNDEFINED:
    pass


def identity(x):
    return x


def comp(*funcs):
    return functools.reduce(lambda f, g: lambda x: f(g(x)), funcs, identity)


def multi_arity(*funcs):
    def dispatch(*args):
        try:
            func = funcs[len(args)]
            if func is None:
                raise IndexError
        except IndexError:
            raise TypeError('wrong number of arguments supplied')
        return func(*args)
    return dispatch


class _Reduced:
    def __init__(self, value):
        self.value = value


def reduced(value):
    return _Reduced(value)


def is_reduced(value):
    return isinstance(value, _Reduced)


def ensure_reduced(x):
    return x if is_reduced(x) else reduced(x)


def unreduced(x):
    return x.value if is_reduced(x) else x


def completing(rf, cf=identity):
    @functools.wraps(rf)
    def wrapper(*args):
        if len(args) == 1:
            return cf(*args)
        return rf(*args)
    return wrapper


def _ireduce(rf, init, coll):
    result = init
    for x in coll:
        result = rf(result, x)
        if is_reduced(result):
            return unreduced(result)
    return result


def ireduce(rf, init, coll=_UNDEFINED):
    if coll is _UNDEFINED:
        return _ireduce(rf, rf(), init)
    return _ireduce(rf, init, coll)


def _itransduce(xform, rf, init, coll):
    xrf = xform(rf)
    return xrf(ireduce(xrf, init, coll))


def itransduce(xform, rf, init, coll=_UNDEFINED):
    if coll is _UNDEFINED:
        return _itransduce(xform, rf, rf(), init)
    return _itransduce(xform, rf, init, coll)


def xiter(xform, coll):
    buffer = deque()

    def flush_buffer(buf):
        assert buf is buffer, 'xform returned invalid value'
        while len(buf) > 0:
            yield buf.popleft()

    rf = xform(multi_arity(None,
                           identity,
                           lambda result, val: result.append(val) or result))
    for x in coll:
        ret = rf(buffer, x)
        yield from flush_buffer(unreduced(ret))
        if is_reduced(ret):
            break

    yield from flush_buffer(rf(buffer))


def map(f):
    return lambda rf: multi_arity(rf, rf,
                                  lambda result, val: rf(result, f(val)))


def map_indexed(f):
    def xform(rf):
        i = -1

        def step(result, val):
            nonlocal i
            i += 1
            return rf(result, f(i, val))

        return multi_arity(rf, rf, step)
    return xform


def filter(pred):
    return lambda rf: multi_arity(rf, rf,
                                  lambda result, val: (rf(result, val)
                                                       if pred(val)
                                                       else result))


def filter_indexed(pred):
    return comp(map_indexed(lambda i, x: x if pred(i, x) else _UNDEFINED),
                filter(lambda x: x is not _UNDEFINED))


def remove(pred):
    return filter(lambda x: not pred(x))


def remove_indexed(pred):
    return filter_indexed(lambda i, x: not pred(i, x))


def keep(f):
    return comp(map(f), filter(lambda x: x is not None))


def keep_indexed(f):
    return comp(map_indexed(f), filter(lambda x: x is not None))


def cat(rf):
    return multi_arity(rf, rf, functools.partial(ireduce, rf))


def mapcat(f):
    return comp(map(f), cat)


def take(n):
    def xform(rf):
        remaining = n

        def step(result, val):
            nonlocal remaining
            new_result = rf(result, val) if remaining > 0 else result
            remaining -= 1
            return ensure_reduced(new_result) if remaining <= 0 else new_result

        return multi_arity(rf, rf, step)
    return xform


def take_last(n):
    def xform(rf):
        buffer = deque()

        def step(result, val):
            buffer.append(val)
            if len(buffer) > n:
                buffer.popleft()
            return result

        def complete(result):
            new_result = result
            while len(buffer) > 0:
                new_result = rf(new_result, buffer.popleft())
                if is_reduced(new_result):
                    buffer.clear()
            return rf(unreduced(new_result))

        return multi_arity(rf, complete, step)
    return xform


def take_nth(n):
    if n < 1 or n != int(n):
        raise ValueError('n must be a nonnegative integer')
    return filter_indexed(lambda i, _: i % n == 0)


def take_while(pred):
    def xform(rf):
        return multi_arity(rf, rf, lambda result, val: (rf(result, val)
                                                        if pred(val)
                                                        else reduced(result)))
    return xform


def drop(n):
    def xform(rf):
        remaining = n

        def step(result, val):
            nonlocal remaining
            remaining -= 1
            return result if remaining > -1 else rf(result, val)

        return multi_arity(rf, rf, step)
    return xform


def drop_last(n):
    def xform(rf):
        buffer = deque()

        def step(result, val):
            buffer.append(val)
            if len(buffer) > n:
                return rf(result, buffer.popleft())
            return result

        def complete(result):
            buffer.clear()
            return rf(result)

        return multi_arity(rf, complete, step)
    return xform


def drop_while(pred):
    def xform(rf):
        has_taken = False

        def step(result, val):
            nonlocal has_taken

            if not has_taken and pred(val):
                return result

            has_taken = True
            return rf(result, val)

        return multi_arity(rf, rf, step)
    return xform


def distinct(rf):
    prev_vals = set()

    def step(result, val):
        if val in prev_vals:
            return result
        prev_vals.add(val)
        return rf(result, val)

    def complete(result):
        prev_vals.clear()
        return rf(result)

    return multi_arity(rf, complete, step)


def dedupe(rf):
    prev_val = _UNDEFINED

    def step(result, val):
        nonlocal prev_val
        if val == prev_val:
            return result
        prev_val = val
        return rf(result, val)

    return multi_arity(rf, rf, step)


def partition_all(n, step=None):
    if step is None:
        step = n
    if n < 1 or n != int(n):
        raise ValueError('n must be a nonnegative integer')
    if step < 1 or step != int(step):
        raise ValueError('step must be a nonnegative integer')

    def xform(rf):
        buffer = []
        remaining_drops = 0

        def step_f(result, val):
            nonlocal buffer, remaining_drops

            if remaining_drops > 0:
                remaining_drops -= 1
                return result

            buffer.append(val)
            if len(buffer) < n:
                return result

            buf = tuple(buffer)
            buffer = buffer[step:]
            remaining_drops = max(0, step - n)
            return rf(result, buf)

        def complete(result):
            nonlocal buffer
            new_result = result

            while len(buffer) > 0:
                buf = tuple(buffer)
                buffer = buffer[step:]
                new_result = rf(new_result, buf)
                if is_reduced(new_result):
                    buffer.clear()

            return rf(unreduced(new_result))

        return multi_arity(rf, complete, step_f)
    return xform


def partition(n, step=None, pad=None):
    def pad_xform(rf):
        def step_f(result, val):
            if len(val) < n:
                if pad is None:
                    return reduced(result)
                padding = tuple(itertools.islice(pad, n - len(val)))
                return ensure_reduced(rf(result, val + tuple(padding)))
            return rf(result, val)

        return multi_arity(rf, rf, step_f)
    return comp(partition_all(n, step), pad_xform)


def partition_by(f):
    def xform(rf):
        prev_ret = _UNDEFINED
        buffer = []

        def step(result, val):
            nonlocal prev_ret, buffer

            ret = f(val)
            if prev_ret is _UNDEFINED or ret == prev_ret:
                prev_ret = ret
                buffer.append(val)
                return result

            prev_ret = ret
            buf = tuple(buffer)
            buffer = [val]
            return rf(result, buf)

        def complete(result):
            if len(buffer) == 0:
                return rf(result)
            flushed_result = unreduced(rf(result, tuple(buffer)))
            buffer.clear()
            return rf(flushed_result)

        return multi_arity(rf, complete, step)
    return xform


def reductions(f, init):
    def xform(rf):
        prev_state = _UNDEFINED

        def step(result, val):
            nonlocal prev_state

            if prev_state is _UNDEFINED:
                prev_state = init
                result = rf(result, init)
                if is_reduced(result):
                    return result

            prev_state = f(prev_state, val)
            new_result = rf(result, unreduced(prev_state))
            return (ensure_reduced(new_result)
                    if is_reduced(prev_state)
                    else new_result)

        def complete(result):
            if prev_state is _UNDEFINED:
                tmp_result = unreduced(rf(result, init))
            else:
                tmp_result = result
            return rf(tmp_result)

        return multi_arity(rf, complete, step)
    return xform


def interpose(sep):
    def xform(rf):
        is_initial = True

        def step(result, val):
            nonlocal is_initial
            if is_initial:
                is_initial = False
                return rf(result, val)
            sep_result = rf(result, sep)
            return rf(sep_result, val)

        return multi_arity(rf, rf, step)
    return xform


def replace(smap):
    def xform(rf):
        def step(result, val):
            return rf(result, smap.get(val, val))

        return multi_arity(rf, rf, step)
    return xform


def random_sample(prob):
    return filter(lambda _: random.random() < prob)