# The code in this file was initially retrieved from the "Werkzeug" project. It
# was modified to fit our needs, primarily by removing anything that wasn't used
# by the `LocalProxy` class and addressing warnings issued by ruff.
#
# Retrieved from:
#  https://github.com/pallets/werkzeug/blob/504a8c4fbda9b8b2fd09e817544ffd228f23458e/src/werkzeug/local.py
#
# Copyright 2007 Pallets
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1.  Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
#
# 2.  Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#
# 3.  Neither the name of the copyright holder nor the names of its
#     contributors may be used to endorse or promote products derived from
#     this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import copy
import math
import operator
import typing as t
from contextvars import ContextVar
from functools import partial
from operator import attrgetter
from types import FunctionType

T = t.TypeVar("T")
F = t.TypeVar("F", bound=t.Callable[..., t.Any])


class _ProxyLookup:
    """Descriptor that handles proxied attribute lookup for
    :class:`LocalProxy`.

    :param f: The built-in function this attribute is accessed through.
        Instead of looking up the special method, the function call
        is redone on the object.
    :param fallback: Return this function if the proxy is unbound
        instead of raising a :exc:`RuntimeError`.
    :param is_attr: This proxied name is an attribute, not a function.
        Call the fallback immediately to get the value.
    :param class_value: Value to return when accessed from the
        ``LocalProxy`` class directly. Used for ``__doc__`` so building
        docs still works.
    """

    __slots__ = ("bind_f", "class_value", "fallback", "is_attr", "name")

    def __init__(
        self,
        f: t.Callable[..., t.Any] | None = None,
        fallback: t.Callable[[LocalProxy[t.Any]], t.Any] | None = None,
        class_value: t.Any | None = None,
        is_attr: bool = False,
    ) -> None:
        bind_f: t.Callable[[LocalProxy[t.Any], t.Any], t.Callable[..., t.Any]] | None

        if hasattr(f, "__get__"):
            # A Python function, can be turned into a bound method.

            def bind_f(
                _instance: LocalProxy[t.Any], obj: t.Any
            ) -> t.Callable[..., t.Any]:
                return f.__get__(obj, type(obj))  # type: ignore

        elif f is not None:
            # A C function, use partial to bind the first argument.

            def bind_f(
                _instance: LocalProxy[t.Any], obj: t.Any
            ) -> t.Callable[..., t.Any]:
                return partial(f, obj)

        else:
            # Use getattr, which will produce a bound method.
            bind_f = None

        self.bind_f = bind_f
        self.fallback = fallback
        self.class_value = class_value
        self.is_attr = is_attr

    def __set_name__(self, owner: LocalProxy[t.Any], name: str) -> None:
        self.name = name

    def __get__(
        self, instance: LocalProxy[t.Any] | None, owner: type | None = None
    ) -> t.Any:
        if instance is None:
            if self.class_value is not None:
                return self.class_value

            return self

        try:
            obj = instance._get_current_object()
        except RuntimeError:
            if self.fallback is None:
                raise

            # The fallback callbacks supplied below are Python functions.
            fallback = t.cast(FunctionType, self.fallback).__get__(instance, owner)

            if self.is_attr:
                # __class__ and __doc__ are attributes, not methods.
                # Call the fallback to get the value.
                return fallback()

            return fallback

        if self.bind_f is not None:
            return self.bind_f(instance, obj)

        return getattr(obj, self.name)

    def __repr__(self) -> str:
        return f"proxy {self.name}"

    def __call__(
        self, instance: LocalProxy[t.Any], *args: t.Any, **kwargs: t.Any
    ) -> t.Any:
        """Support calling unbound methods from the class. For example,
        this happens with ``copy.copy``, which does
        ``type(x).__copy__(x)``. ``type(x)`` can't be proxied, so it
        returns the proxy type and descriptor.
        """
        return self.__get__(instance, type(instance))(*args, **kwargs)


class _ProxyIOp(_ProxyLookup):
    """Look up an augmented assignment method on a proxied object. The
    method is wrapped to return the proxy instead of the object.
    """

    __slots__ = ()

    def __init__(
        self,
        f: t.Callable[..., t.Any] | None = None,
        fallback: t.Callable[[LocalProxy[t.Any]], t.Any] | None = None,
    ) -> None:
        super().__init__(f, fallback)

        def bind_f(instance: LocalProxy[t.Any], obj: t.Any) -> t.Callable[..., t.Any]:
            def i_op(self: t.Any, other: t.Any) -> LocalProxy[t.Any]:
                f(self, other)  # type: ignore
                return instance

            return i_op.__get__(obj, type(obj))

        self.bind_f = bind_f  # type: ignore[assignment]


def _l_to_r_op[T, R](op: t.Callable[[T, T], R]) -> t.Callable[[T, T], R]:
    """Swap the argument order to turn an l-op into an r-op."""

    def r_op(obj: T, other: T) -> R:
        return op(other, obj)

    return r_op


def _identity[T](o: T) -> T:
    return o


class LocalProxy[T]:
    """A proxy to the object bound to a context-local object. All
    operations on the proxy are forwarded to the bound object. If no
    object is bound, a ``RuntimeError`` is raised.

    :param local: The context-local object that provides the proxied
        object.
    :param name: Proxy this attribute from the proxied object.
    :param unbound_message: The error message to show if the
        context-local object is unbound.

    Proxy a :class:`~contextvars.ContextVar` to make it easier to
    access. Pass a name to proxy that attribute.

    .. code-block:: python

        _request_var = ContextVar("request")
        request = LocalProxy(_request_var)
        session = LocalProxy(_request_var, "session")

    Proxy an attribute on a :class:`Local` namespace by calling the
    local with the attribute name:

    .. code-block:: python

        data = Local()
        user = data("user")

    Proxy the top item on a :class:`LocalStack` by calling the local.
    Pass a name to proxy that attribute.

    .. code-block::

        app_stack = LocalStack()
        current_app = app_stack()
        g = app_stack("g")

    Pass a function to proxy the return value from that function. This
    was previously used to access attributes of local objects before
    that was supported directly.

    .. code-block:: python

        session = LocalProxy(lambda: request.session)

    ``__repr__`` and ``__class__`` are proxied, so ``repr(x)`` and
    ``isinstance(x, cls)`` will look like the proxied object. Use
    ``issubclass(type(x), LocalProxy)`` to check if an object is a
    proxy.

    .. code-block:: python

        repr(user)  # <User admin>
        isinstance(user, User)  # True
        issubclass(type(user), LocalProxy)  # True

    .. versionchanged:: 2.2.2
        ``__wrapped__`` is set when wrapping an object, not only when
        wrapping a function, to prevent doctest from failing.

    .. versionchanged:: 2.2
        Can proxy a ``ContextVar`` or ``LocalStack`` directly.

    .. versionchanged:: 2.2
        The ``name`` parameter can be used with any proxied object, not
        only ``Local``.

    .. versionchanged:: 2.2
        Added the ``unbound_message`` parameter.

    .. versionchanged:: 2.0
        Updated proxied attributes and methods to reflect the current
        data model.

    .. versionchanged:: 0.6.1
        The class can be instantiated with a callable.
    """

    __slots__ = ("__wrapped", "_get_current_object")

    _get_current_object: t.Callable[[], T]
    """Return the current object this proxy is bound to. If the proxy is
    unbound, this raises a ``RuntimeError``.

    This should be used if you need to pass the object to something that
    doesn't understand the proxy. It can also be useful for performance
    if you are accessing the object multiple times in a function, rather
    than going through the proxy multiple times.
    """

    def __init__(
        self,
        local: ContextVar[T] | t.Callable[[], T],
        name: str | None = None,
        *,
        unbound_message: str | None = None,
    ) -> None:
        if name is None:
            get_name = _identity
        else:
            get_name = attrgetter(name)  # type: ignore[assignment]

        if unbound_message is None:
            unbound_message = "object is not bound"

        if isinstance(local, ContextVar):

            def _get_current_object() -> T:
                try:
                    obj = local.get()
                except LookupError:
                    raise RuntimeError(unbound_message) from None

                return get_name(obj)

        elif callable(local):

            def _get_current_object() -> T:
                return get_name(local())

        else:
            raise TypeError(f"Don't know how to proxy '{type(local)}'.")

        object.__setattr__(self, "_LocalProxy__wrapped", local)
        object.__setattr__(self, "_get_current_object", _get_current_object)

    __doc__ = _ProxyLookup(
        class_value=__doc__, fallback=lambda self: type(self).__doc__, is_attr=True
    )
    __wrapped__ = _ProxyLookup(
        fallback=lambda self: self._LocalProxy__wrapped,  # type: ignore[attr-defined]
        is_attr=True,
    )
    # __del__ should only delete the proxy
    __repr__ = _ProxyLookup(
        repr, fallback=lambda self: f"<{type(self).__name__} unbound>"
    )
    __str__ = _ProxyLookup(str)
    __bytes__ = _ProxyLookup(bytes)
    __format__ = _ProxyLookup()
    __lt__ = _ProxyLookup(operator.lt)
    __le__ = _ProxyLookup(operator.le)
    __eq__ = _ProxyLookup(operator.eq)
    __ne__ = _ProxyLookup(operator.ne)
    __gt__ = _ProxyLookup(operator.gt)
    __ge__ = _ProxyLookup(operator.ge)
    __hash__ = _ProxyLookup(hash)
    __bool__ = _ProxyLookup(bool, fallback=lambda _self: False)
    __getattr__ = _ProxyLookup(getattr)
    # __getattribute__ triggered through __getattr__
    __setattr__ = _ProxyLookup(setattr)
    __delattr__ = _ProxyLookup(delattr)
    __dir__ = _ProxyLookup(dir, fallback=lambda _self: [])
    # __get__ (proxying descriptor not supported)
    # __set__ (descriptor)
    # __delete__ (descriptor)
    # __set_name__ (descriptor)
    # __objclass__ (descriptor)
    # __slots__ used by proxy itself
    # __dict__ (__getattr__)
    # __weakref__ (__getattr__)
    # __init_subclass__ (proxying metaclass not supported)
    # __prepare__ (metaclass)
    __class__ = _ProxyLookup(fallback=lambda self: type(self), is_attr=True)
    __instancecheck__ = _ProxyLookup(lambda self, other: isinstance(other, self))
    __subclasscheck__ = _ProxyLookup(lambda self, other: issubclass(other, self))
    # __class_getitem__ triggered through __getitem__
    __call__ = _ProxyLookup(lambda self, *args, **kwargs: self(*args, **kwargs))
    __len__ = _ProxyLookup(len)
    __length_hint__ = _ProxyLookup(operator.length_hint)
    __getitem__ = _ProxyLookup(operator.getitem)
    __setitem__ = _ProxyLookup(operator.setitem)
    __delitem__ = _ProxyLookup(operator.delitem)
    # __missing__ triggered through __getitem__
    __iter__ = _ProxyLookup(iter)
    __next__ = _ProxyLookup(next)
    __reversed__ = _ProxyLookup(reversed)
    __contains__ = _ProxyLookup(operator.contains)
    __add__ = _ProxyLookup(operator.add)
    __sub__ = _ProxyLookup(operator.sub)
    __mul__ = _ProxyLookup(operator.mul)
    __matmul__ = _ProxyLookup(operator.matmul)
    __truediv__ = _ProxyLookup(operator.truediv)
    __floordiv__ = _ProxyLookup(operator.floordiv)
    __mod__ = _ProxyLookup(operator.mod)
    __divmod__ = _ProxyLookup(divmod)
    __pow__ = _ProxyLookup(pow)
    __lshift__ = _ProxyLookup(operator.lshift)
    __rshift__ = _ProxyLookup(operator.rshift)
    __and__ = _ProxyLookup(operator.and_)
    __xor__ = _ProxyLookup(operator.xor)
    __or__ = _ProxyLookup(operator.or_)
    __radd__ = _ProxyLookup(_l_to_r_op(operator.add))
    __rsub__ = _ProxyLookup(_l_to_r_op(operator.sub))
    __rmul__ = _ProxyLookup(_l_to_r_op(operator.mul))
    __rmatmul__ = _ProxyLookup(_l_to_r_op(operator.matmul))
    __rtruediv__ = _ProxyLookup(_l_to_r_op(operator.truediv))
    __rfloordiv__ = _ProxyLookup(_l_to_r_op(operator.floordiv))
    __rmod__ = _ProxyLookup(_l_to_r_op(operator.mod))
    __rdivmod__ = _ProxyLookup(_l_to_r_op(divmod))
    __rpow__ = _ProxyLookup(_l_to_r_op(pow))
    __rlshift__ = _ProxyLookup(_l_to_r_op(operator.lshift))
    __rrshift__ = _ProxyLookup(_l_to_r_op(operator.rshift))
    __rand__ = _ProxyLookup(_l_to_r_op(operator.and_))
    __rxor__ = _ProxyLookup(_l_to_r_op(operator.xor))
    __ror__ = _ProxyLookup(_l_to_r_op(operator.or_))
    __iadd__ = _ProxyIOp(operator.iadd)
    __isub__ = _ProxyIOp(operator.isub)
    __imul__ = _ProxyIOp(operator.imul)
    __imatmul__ = _ProxyIOp(operator.imatmul)
    __itruediv__ = _ProxyIOp(operator.itruediv)
    __ifloordiv__ = _ProxyIOp(operator.ifloordiv)
    __imod__ = _ProxyIOp(operator.imod)
    __ipow__ = _ProxyIOp(operator.ipow)
    __ilshift__ = _ProxyIOp(operator.ilshift)
    __irshift__ = _ProxyIOp(operator.irshift)
    __iand__ = _ProxyIOp(operator.iand)
    __ixor__ = _ProxyIOp(operator.ixor)
    __ior__ = _ProxyIOp(operator.ior)
    __neg__ = _ProxyLookup(operator.neg)
    __pos__ = _ProxyLookup(operator.pos)
    __abs__ = _ProxyLookup(abs)
    __invert__ = _ProxyLookup(operator.invert)
    __complex__ = _ProxyLookup(complex)
    __int__ = _ProxyLookup(int)
    __float__ = _ProxyLookup(float)
    __index__ = _ProxyLookup(operator.index)
    __round__ = _ProxyLookup(round)
    __trunc__ = _ProxyLookup(math.trunc)
    __floor__ = _ProxyLookup(math.floor)
    __ceil__ = _ProxyLookup(math.ceil)
    __enter__ = _ProxyLookup()
    __exit__ = _ProxyLookup()
    __await__ = _ProxyLookup()
    __aiter__ = _ProxyLookup()
    __anext__ = _ProxyLookup()
    __aenter__ = _ProxyLookup()
    __aexit__ = _ProxyLookup()
    __copy__ = _ProxyLookup(copy.copy)
    __deepcopy__ = _ProxyLookup(copy.deepcopy)
    # __getnewargs_ex__ (pickle through proxy not supported)
    # __getnewargs__ (pickle)
    # __getstate__ (pickle)
    # __setstate__ (pickle)
    # __reduce__ (pickle)
    # __reduce_ex__ (pickle)
