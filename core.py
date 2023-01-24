import itertools
from abc import ABC, abstractmethod
from collections import defaultdict
from copy import copy
from functools import cached_property
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Generator,
    Generic,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from typing_extensions import Self
from time import time

__all__ = (
    "NiceCollection",
    "NiceDocument",
    "field",
    "field_with_list",
    "field_with_set",
    "field_with_dict",
    "field_with_nestings",
    "nesting",
)

T1 = TypeVar("T1")
T2 = TypeVar("T2")
V1 = TypeVar("V1")
V2 = TypeVar("V2")
SizedIterableT = TypeVar("SizedIterableT", bound=Union[list, set])
NiceDocumentT = TypeVar("NiceDocumentT", bound="NiceDocument")
NiceNestingT = TypeVar("NiceNestingT", bound="NiceNesting")


class _MissingSentinel:
    def __eq__(self, other: Any) -> bool:
        return False

    def __hash__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "..."


MISSING: Any = _MissingSentinel()


class AsyncCollection:
    """A class for type hinting purposes."""

    def find(self, *args, **kwargs) -> AsyncIterator[Any]:
        ...

    async def find_one(self, *args, **kwargs) -> Any:
        ...

    async def update_one(self, *args, **kwargs) -> Any:
        ...

    async def delete_one(self, *args, **kwargs) -> Any:
        ...


class FieldExtractingMeta(type):
    def __new__(cls, name: str, bases: Tuple[type, ...], _dict: Dict[str, Any]):
        fields = {}
        nice_nestings = {}
        invalidated_attrs = []

        for name, field in _dict.items():
            if isinstance(field, (NestingFactory, FieldWithNestings)):
                nice_nestings[name] = field
                invalidated_attrs.append(name)
            elif isinstance(field, FieldBase):
                fields[name] = field
                invalidated_attrs.append(name)

        for name in invalidated_attrs:
            _dict.pop(name, None)

        _dict["_fields"] = fields
        _dict["_nice_nestings"] = nice_nestings
        return super().__new__(cls, name, bases, _dict)


class FieldBase(ABC):
    """A base class for all field variations.
    Feilds are essentially just converters from raw json-like data to arbitrary python objects and back.
    They should be assigned to class vars of `NiceDocument` or `NiceNesting` subclasses.
    At runtime, these classvars are deleted and replaced with attributes that hold converted data for each instance
    of `NiceDocument` or `NiceNesting`.
    """

    def __init__(self, default: Any = None, alias_for: Optional[str] = None):
        self._default: Any = default
        self.real_name: Optional[str] = alias_for

    @property
    def default(self) -> Any:
        return copy(self._default)

    @abstractmethod
    def from_raw(self, value: Any) -> Any:
        ...

    @abstractmethod
    def to_raw(self, value: Any) -> Any:
        ...


class Field(FieldBase):
    def __init__(
        self,
        default: Any = None,
        from_raw: Optional[Callable[[T1], T2]] = None,
        to_raw: Optional[Callable[[T2], T1]] = None,
        alias_for: Optional[str] = None,
    ):
        super().__init__(default, alias_for)
        identity = lambda x: x
        self._from_raw: Callable[[T1], T2] = from_raw or identity
        self._to_raw: Callable[[T2], T1] = to_raw or identity

    def from_raw(self, value):
        if value is None:
            return self.default

        return self._from_raw(value)

    def to_raw(self, value):
        if value == self._default:
            return None

        return self._to_raw(value)


class FieldWithContainer(FieldBase, Generic[SizedIterableT]):
    def __init__(
        self,
        sized_iterable: Type[SizedIterableT],
        default: Any = ...,
        from_raw_element: Optional[Callable[[T1], T2]] = None,
        to_raw_element: Optional[Callable[[T2], T1]] = None,
        alias_for: Optional[str] = None,
    ):
        if default is Ellipsis:
            default = sized_iterable()
        super().__init__(default, alias_for)
        id_: Callable[[Any], Any] = lambda x: x
        self._sized_iterable: Type[SizedIterableT] = sized_iterable
        self.from_raw_element: Callable[[T1], T2] = from_raw_element or id_
        self.to_raw_element: Callable[[T2], T1] = to_raw_element or id_

    def from_raw(self, value: Optional[SizedIterableT]):
        if value is None:
            return self.default

        return self._sized_iterable(self.from_raw_element(el) for el in value)

    def to_raw(self, value: SizedIterableT):
        return self._sized_iterable(self.to_raw_element(el) for el in value)


class FieldWithDict(FieldBase):
    def __init__(
        self,
        default: Any = ...,
        from_raw_item: Optional[Callable[[T1, V1], Tuple[T2, V2]]] = None,
        to_raw_item: Optional[Callable[[T2, V2], Tuple[T1, V1]]] = None,
        alias_for: Optional[str] = None,
    ):
        if default is Ellipsis:
            default = {}
        super().__init__(default, alias_for)
        id_ = lambda x, y: (x, y)
        self.from_raw_item: Callable[[T1, V1], Tuple[T2, V2]] = from_raw_item or id_
        self.to_raw_item: Callable[[T2, V2], Tuple[T1, V1]] = to_raw_item or id_

    def from_raw(self, data: Optional[dict]) -> dict:
        if data is None:
            return self.default

        return dict(self.from_raw_item(k, v) for k, v in data.items())

    def to_raw(self, data: dict) -> dict:
        return dict(self.to_raw_item(k, v) for k, v in data.items())


class FieldWithNestings(FieldBase):
    def __init__(
        self,
        cls: Type["NiceNesting"],
        default: Any = ...,
        from_raw_key: Optional[Callable[[Any], Any]] = None,
        to_raw_key: Optional[Callable[[Any], Any]] = None,
        alias: Optional[str] = None,
    ):
        if default is Ellipsis:
            default = {}
        super().__init__(default, alias)
        self.cls: Type[NiceNesting] = cls
        self.from_raw_key: Callable[[Any], Any] = from_raw_key or (lambda x: x)
        self.to_raw_key: Callable[[Any], Any] = to_raw_key or (lambda x: x)

    def from_raw(
        self, data: Optional[dict], attr_name: str, parent: "NiceNesting"
    ) -> dict:
        if data is None:
            return self.default

        return {
            self.from_raw_key(k): self.cls(
                attr_name="",
                data=v,
                parent=parent,
                alias=f"{self.real_name or attr_name}.{k}",
            )
            for k, v in data.items()
        }

    def to_raw(self, _) -> Any:
        # this must never be used with nestings
        raise NotImplementedError


class NestingFactory(FieldBase):
    def __init__(
        self,
        cls: Type["NiceNesting"],
        alias: Optional[str] = None,
    ):
        super().__init__(..., alias)
        self.cls: Type[NiceNesting] = cls

    def from_raw(
        self, data: Optional[dict], attr_name: str, parent: "NiceNesting"
    ) -> "NiceNesting":
        return self.cls(
            attr_name=attr_name, data=data, parent=parent, alias=self.real_name
        )

    def to_raw(self, _) -> Any:
        # this must never be used with nestings
        raise NotImplementedError


class CommandMaker:
    """This class is used to generate and apply a mongo command
    based on a sequence of updates of attributes inside of an async context manager.

    This class is not meant to be used explicitly. The actual use case is this:

    ```python
    async with doc.command_maker() as cmd:
        cmd.age = 21
        cmd.items.append("Headphones")
    ```

    Here `cmd` is an instance of `CommandMaker`, even though it's annotated as NiceDocument.
    """

    __slots__ = (
        "_underlying",
        "_underlying_owner",
        "_name",
        "_value",
        "_to_inc",
        "_to_add",
        "_to_remove",
        "_to_update",
        "_to_pop",
        "_pseudo_nestings",
        "_pseudo_attrs",
    )

    def __init__(
        self,
        name: str = "",
        value: Any = MISSING,
        *,
        underlying: Any = None,
        underlying_owner: Any = None,
    ):
        self._underlying: Any = underlying
        self._underlying_owner: Any = underlying_owner
        self._name: str = name
        self._value: Any = value
        self._to_inc: Any = None
        self._to_add: List[Any] = []
        self._to_remove: List[Any] = []
        self._to_update: Dict[Any, Any] = {}
        self._to_pop: List[Any] = []
        self._pseudo_nestings: Dict[Any, CommandMaker] = {}
        self._pseudo_attrs: Dict[str, CommandMaker] = {}

    def __getattr__(self, name: str) -> Any:
        # this dunder method is called AFTER __getattribute__,
        # thus it doesn't cause any issues with getting normal attributes.
        if name in self._pseudo_attrs:
            return self._pseudo_attrs[name]
        # this also ensures that this attribute exists in the corresponding model
        sub_underlying = getattr(self._underlying, name)
        sub_magic = self.__class__(
            name, underlying=sub_underlying, underlying_owner=self._underlying
        )
        self._pseudo_attrs[name] = sub_magic
        return sub_magic

    def __setattr__(self, name: str, value: Any) -> None:
        # We want to maintain the original __setattr__ behaviour for existing attributes.
        # For fake attributes we want special behaviour.
        if name in self.__slots__:
            return object.__setattr__(self, name, value)

        if isinstance(value, CommandMaker):
            # ideally this happens only after __iadd__
            return

        sub_magic = self._pseudo_attrs.get(name)
        if sub_magic is not None:
            sub_magic._value = value
            return None
        # this also ensures that this attribute exists in the corresponding model
        sub_underlying = getattr(self._underlying, name)
        self._pseudo_attrs[name] = self.__class__(
            name, value, underlying=sub_underlying, underlying_owner=self._underlying
        )

    def __getitem__(self, key: Any) -> Any:
        # Once we're here, it means that this instance fakes a dict attribute.
        # The only thing we should take care of is the possibility that
        # the real value under this key is a nesting.
        if not isinstance(self._underlying_owner, NiceNesting):
            raise SyntaxError("You're not supposed to get items of this attribute")

        _field = self._underlying_owner._nice_nestings.get(self._name)
        if not isinstance(_field, FieldWithNestings):
            raise SyntaxError("You're not supposed to get items of this attribute")

        if key in self._pseudo_nestings:
            return self._pseudo_nestings[key]

        sub_underlying = self._underlying.get(key)
        # at this point we know that this attribute is generated by _field
        if sub_underlying is None:
            sub_underlying = _field.cls(
                attr_name="",
                data=None,
                parent=self._underlying_owner,
                alias=f"{_field.real_name or self._name}.{_field.to_raw_key(key)}",
            )

        sub_magic = self.__class__(
            str(key), underlying=sub_underlying, underlying_owner=self._underlying
        )
        self._pseudo_nestings[key] = sub_magic
        return sub_magic

    def __setitem__(self, key: Any, value: Any) -> None:
        # Once we're here, it means that this instance fakes a dict attribute.
        # Let's take care of assigning the value:
        if isinstance(value, NiceNesting):
            raise ValueError(
                "You're not supposed to directly assign nestings to dict keys. "
                "Modify the attributes of nestings directly: cmd.things[key].attr = value"
            )
        self._to_update[key] = value

    def __iadd__(self, value: Any) -> Any:
        self._to_inc = value
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc: Any, *_) -> None:
        if exc is not None:
            return

        mongo_command = {}
        setters = {}
        unsetters = {}
        adders = defaultdict(list)
        removers = defaultdict(list)

        for magic in self._iter_leaves():
            to_set = magic._value
            to_add = magic._to_add
            to_remove = magic._to_remove
            to_update = magic._to_update
            to_pop = magic._to_pop
            to_inc = magic._to_inc

            underlying_owner = magic._underlying_owner
            if not isinstance(underlying_owner, NiceNesting):
                raise ValueError("Updating irrelevant attributes is not allowed")
            # this also ensures that the user updated an existing field
            field = underlying_owner._fields.get(magic._name)
            if field is None:
                field = underlying_owner._nice_nestings[magic._name]

            route = underlying_owner.route_prefix + (field.real_name or magic._name)

            if to_set is Ellipsis:
                unsetters[route] = ""
                setattr(underlying_owner, magic._name, field.default)

            elif to_set is not MISSING:
                setters[route] = field.to_raw(to_set)
                setattr(underlying_owner, magic._name, to_set)

            if to_inc is not None:
                # we're not going to use "$inc" because we should still support conversion
                # (e.g. string to int and back)
                new_value = getattr(underlying_owner, magic._name) + to_inc
                setters[route] = field.to_raw(new_value)
                setattr(underlying_owner, magic._name, new_value)

            containter = None

            if to_add:
                adders[route].extend(field.to_raw(to_add))
                containter = getattr(underlying_owner, magic._name)
                if isinstance(containter, list):
                    containter.extend(to_add)
                elif isinstance(containter, set):
                    containter.update(to_add)

            if to_remove:
                containter = containter or getattr(underlying_owner, magic._name)
                removers[route].extend(field.to_raw(to_remove))
                for el in to_remove:
                    containter.remove(el)

            dict_attr = None

            if to_update:
                if not isinstance(field, FieldWithDict):
                    raise SyntaxError(
                        "Updating items of a field without items is not allowed"
                    )
                dict_attr = getattr(underlying_owner, magic._name)
                for key, val in to_update.items():
                    dict_attr[key] = val
                    raw_key, raw_value = field.to_raw_item(key, val)
                    setters[f"{route}.{raw_key}"] = raw_value

            if to_pop:
                dict_attr = dict_attr or getattr(underlying_owner, magic._name)

                if isinstance(field, FieldWithDict):
                    to_raw_key = lambda x: field.to_raw_item(x, dict_attr.get(x))[0]  # type: ignore
                elif isinstance(field, FieldWithNestings):
                    to_raw_key = field.to_raw_key
                else:
                    raise SyntaxError(
                        "Removing items from a field without items is not allowed"
                    )

                for key in to_pop:
                    raw_key = to_raw_key(key)
                    dict_attr.pop(key, None)
                    unsetters[f"{route}.{raw_key}"] = ""

        self._inject_nesting_branches()
        upsert = False

        if setters:
            mongo_command["$set"] = setters
            upsert = True

        if unsetters:
            mongo_command["$unset"] = unsetters

        if adders:
            mongo_command["$addToSet"] = {
                key: ({"$each": value} if len(value) > 1 else value[0])
                for key, value in adders.items()
            }
            upsert = True

        if removers:
            mongo_command["$pull"] = {
                key: ({"$in": value} if len(value) > 1 else value[0])
                for key, value in removers.items()
            }

        if not mongo_command:
            return

        if self._underlying is not None:
            doc = self._underlying.document
            await doc.mongo_col.update_one(
                {"_id": doc.id}, mongo_command, upsert=upsert
            )
            return

        raise ValueError(
            "CommandMaker is unable to find a mongo-collection instance to make a request"
        )

    def _iter_leaves(self) -> Generator["CommandMaker", None, None]:
        for magic in itertools.chain(
            self._pseudo_attrs.values(), self._pseudo_nestings.values()
        ):
            if magic._pseudo_attrs:
                yield from magic._iter_leaves()
            elif magic._pseudo_nestings:
                yield from magic._iter_leaves()
            else:
                yield magic

    def _inject_nesting_branches(self) -> None:
        # this is done to bind newly created nestings to their corresponding cached parents
        for key, fake_nesting in self._pseudo_nestings.items():
            if key not in self._underlying:
                self._underlying[key] = fake_nesting._underlying

        for magic in itertools.chain(
            self._pseudo_attrs.values(), self._pseudo_nestings.values()
        ):
            magic._inject_nesting_branches()

    def append(self, obj: Any) -> None:
        """This will append the object right before sending the mongo command"""
        self._to_add.append(obj)

    def extend(self, obj: Any) -> None:
        """This will extend the list right before sending the mongo command"""
        self._to_add.extend(obj)

    def add(self, obj: Any) -> None:
        """This will add the object right before sending the mongo command"""
        self._to_add.append(obj)

    def update(self, obj: Any) -> None:
        """This will extend the set right before sending the mongo command"""
        self._to_add.extend(obj)

    def remove(self, obj: Any) -> None:
        """This will remove the object right before sending the mongo command"""
        self._to_remove.append(obj)

    def pop(self, key: Any, *_) -> None:
        """This will remove the item right before sending the mongo command"""
        self._to_pop.append(key)


class NiceNesting(metaclass=FieldExtractingMeta):
    """The base class for all nestings"""

    _fields: Dict[str, FieldBase]
    _nice_nestings: Dict[str, Union[NestingFactory, FieldWithNestings]]

    def __init__(
        self,
        *,
        attr_name: str,
        data: Optional[Dict[str, Any]],
        parent: Optional["NiceNesting"],
        alias: Optional[str] = None,
    ):
        self._parent: Optional[NiceNesting] = parent
        self._name: str = attr_name
        self._alias: Optional[str] = alias

        if data is None:
            data = {}

        for name, field in self._fields.items():
            use_name = field.real_name or name
            setattr(self, name, field.from_raw(data.get(use_name)))

        for name, field in self._nice_nestings.items():
            use_name = field.real_name or name
            setattr(self, name, field.from_raw(data.get(use_name), name, self))

    async def __aenter__(self) -> Self:
        # this exists to bypass linters since
        # .command_maker() return type is annotated as Self
        raise NotImplementedError

    async def __aexit__(self, *_) -> None:
        # this exists to bypass linters since
        # .command_maker() return type is annotated as Self
        raise NotImplementedError

    @property
    def mongo_name(self) -> str:
        return self._alias or self._name

    @cached_property
    def route_prefix(self) -> str:
        if isinstance(self._parent, NiceDocument):
            return f"{self.mongo_name}."
        elif isinstance(self._parent, NiceNesting):
            return f"{self._parent.route_prefix}{self.mongo_name}."
        raise ValueError(
            f"Invalid parent type of '{self._name}': {self.__class__.__name__}"
        )

    @cached_property
    def document(self) -> "NiceDocument":
        if self._parent is None:
            raise ValueError(
                "This nesting is not usable yet. Add it to a dict of nestings first."
            )

        if isinstance(self._parent, NiceDocument):
            return self._parent
        elif isinstance(self._parent, NiceNesting):
            return self._parent.document
        raise ValueError(
            f"Invalid parent type of '{self._name}': {self.__class__.__name__}"
        )

    @property
    def mongo_col(self) -> AsyncCollection:
        return self.document.mongo_col

    def command_maker(self) -> Self:
        # here the return type is 'Self' because
        # I want IDEs to autocomplete attribute names
        return CommandMaker(self.route_prefix, underlying=self)  # type: ignore

    cmdmk = command_maker


class NiceDocument(NiceNesting):
    """The base class for document wrappers"""

    def __init__(self, data: Dict[str, Any], collection: "NiceCollection"):
        super().__init__(attr_name="", data=data, parent=None)
        self.id: Union[int, str] = data["_id"]
        self.collection: NiceCollection = collection
        self._last_used_at: float = time()

    @property
    def route_prefix(self) -> str:
        return ""

    @property
    def document(self) -> "NiceDocument":
        return self

    @property
    def is_cached(self) -> bool:
        return self.id in self.collection.cache

    @property
    def mongo_col(self) -> AsyncCollection:
        return self.collection.mongo_col

    @classmethod
    def minimal(
        cls: Type[NiceDocumentT], id: Union[int, str], collection: "NiceCollection"
    ) -> NiceDocumentT:
        self = cls.__new__(cls)
        self.id = id
        self.collection = collection

        for name, field in self._fields.items():
            setattr(self, name, field.default)

        for name, field in self._nice_nestings.items():
            setattr(self, name, field.from_raw(None, name, self))

        self._last_used_at = time()
        return self

    @classmethod
    def make_nice_collection(
        cls: Type[NiceDocumentT],
        collection: AsyncCollection,
        cache_lifetime: Optional[float] = None,
    ) -> "NiceCollection[NiceDocumentT]":
        """Make a `NiceCollection` instance that works with this type of documents.

        Parameters
        ----------
        collection: `AsyncCollection`
            The mongodb motor-wrapped collection to use.
        cache_lifetime: Optional[`float`]
            For how many seconds a document should be cached.
            If this parameter is `None`, all documents stay cached forever.
            If 0, nothing gets cached. Defaults to `None`.
        """
        return NiceCollection(collection, cls, cache_lifetime)

    async def delete(self) -> None:
        """Deletes the document from both database and cache."""
        await self.collection.delete(self.id)


class NiceCollection(Generic[NiceDocumentT]):
    """A base class for wrappers of collections.

    Parameters
    ----------
    collection: `AsyncCollection`
        The mongodb motor-wrapped collection to use.
    document_wrapper: `type`
        The wrapper class for documents of this collection.
    cache_lifetime: Optional[`float`]
        For how many seconds a document should be cached.
        If this parameter is `None`, all documents stay cached forever.
        If this parameter is `0`, documents don't get cached.
        Defaults to `None`.
    """

    def __init__(
        self,
        collection: AsyncCollection,
        document_wrapper: Type[NiceDocumentT],
        cache_lifetime: Optional[float] = None,
    ):
        self.mongo_col: AsyncCollection = collection
        self.cache: Dict[Union[int, str], NiceDocumentT] = {}
        self.document_wrapper: Type[NiceDocumentT] = document_wrapper
        self.cache_lifetime: Optional[float] = cache_lifetime
        self._last_cache_check: float = time()

    def _verify_cache_integrity(self) -> None:
        if not self.cache_lifetime:
            # cache_lifetime=None means "never clear cache"
            # cache_lifetime=0 means "nothing is cached"
            return

        now = time()
        if now - self._last_cache_check < 60:
            return
        bad_keys = [
            key
            for key, doc in self.cache.items()
            if now - doc._last_used_at > self.cache_lifetime
        ]
        for key in bad_keys:
            self.cache.pop(key, None)
        self._last_cache_check = now

    def get_cached_or_minimal(self, id: Union[int, str]) -> NiceDocumentT:
        """Returns a cached document with the given ID.
        If nothing was found, creates a minimal mock document.
        Useful when you only need to make a write-request without reading.
        """
        doc = self.cache.get(id)
        if doc is not None:
            doc._last_used_at = time()
            self._verify_cache_integrity()
            return doc

        return self.document_wrapper.minimal(id, self)

    async def find(self, id: Union[int, str]) -> NiceDocumentT:
        """Returns a cached document with the given ID.
        If nothing was found, searches in database, caches and returns the document.
        """
        doc = self.cache.get(id)

        if doc is not None:
            doc._last_used_at = time()
            self._verify_cache_integrity()
            return doc

        self._verify_cache_integrity()

        data = await self.mongo_col.find_one({"_id": id})

        if data is None:
            data = {"_id": id}

        doc = self.document_wrapper(data, self)
        if self.cache_lifetime is None or self.cache_lifetime > 0:
            self.cache[doc.id] = doc

        return doc

    async def delete(self, id: Union[int, str]) -> None:
        """Deletes the document with the given ID from both database and cache."""
        await self.mongo_col.delete_one({"_id": id})
        self.cache.pop(id, None)


def field(
    default: Any = None,
    from_raw: Optional[Callable[[T1], T2]] = None,
    to_raw: Optional[Callable[[T2], T1]] = None,
    alias_for: Optional[str] = None,
) -> Any:
    return Field(default, from_raw, to_raw, alias_for)


def field_with_list(
    default: Any = ...,
    from_raw_element: Optional[Callable[[T1], T2]] = None,
    to_raw_element: Optional[Callable[[T2], T1]] = None,
    alias_for: Optional[str] = None,
) -> Any:
    return FieldWithContainer(
        sized_iterable=list,
        default=default,
        from_raw_element=from_raw_element,
        to_raw_element=to_raw_element,
        alias_for=alias_for,
    )


def field_with_set(
    default: Any = ...,
    from_raw_element: Optional[Callable[[T1], T2]] = None,
    to_raw_element: Optional[Callable[[T2], T1]] = None,
    alias_for: Optional[str] = None,
) -> Any:
    return FieldWithContainer(
        sized_iterable=set,
        default=default,
        from_raw_element=from_raw_element,
        to_raw_element=to_raw_element,
        alias_for=alias_for,
    )


def field_with_dict(
    default: Any = ...,
    from_raw_item: Optional[Callable[[T1, V1], Tuple[T2, V2]]] = None,
    to_raw_item: Optional[Callable[[T2, V2], Tuple[T1, V1]]] = None,
    alias_for: Optional[str] = None,
) -> Any:
    return FieldWithDict(
        default=default,
        from_raw_item=from_raw_item,
        to_raw_item=to_raw_item,
        alias_for=alias_for,
    )


def field_with_nestings(
    cls: Type[NiceNesting],
    default: Any = ...,
    from_raw_key: Optional[Callable[[Any], Any]] = None,
    to_raw_key: Optional[Callable[[Any], Any]] = None,
    alias: Optional[str] = None,
) -> Any:
    return FieldWithNestings(
        cls=cls,
        default=default,
        from_raw_key=from_raw_key,
        to_raw_key=to_raw_key,
        alias=alias,
    )


def nesting(
    cls: Type[NiceNesting],
    alias: Optional[str] = None,
) -> Any:
    return NestingFactory(cls, alias)
