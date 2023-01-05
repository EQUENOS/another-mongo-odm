# Disclaimer

This is an ORM I'm going to use in my projects. I'm publishing this because my friends asked.

Currently this project's state is "proof of concept".


# What is this system

Here `core.py` is the main point of interest. It implements a wrapper of `motor` to allow easy database manipulations. Since mongo reading speeds are low, for a big bot it's important to keep certain data cached and update it in sync with the database. For complex data structures it often is a great pain to take care of, so this is why I decided to make this wrapper for my personal projects.


# Assumptions

This wrapper works under certain assumptions:

- Each collection has documents of uniform structure
- Any array or set contains elements of the same type
- Any field of a document json structure can be missing, except `_id`


# Defining a basic document wrapper

It is as simple as subclassing `NiceDocument` and declaring several classvars using `field`:

```python
from typing import Set

from .core import NiceDocument, field, field_with_set


class User(NiceDocument):
    name: str = field()
    age: int = field()
    items: Set[str] = field_with_set()
```

Here `field` and `field_with_set` might seem unnecessary but they actually have a purpose, I'll mention it later.

Class `MyDoc` will wrap any dict of a relevant structure. Those classvars will be gone and replaced with actual attributes for each instance.


# Using wrappers

First, instantiate a collection somewhere. Example:

```python
cluster = MotorClient(MONGO_TOKEN)
db = cluster["my_db"]
users = User.make_nice_collection(db["users"])
```

Note: ideally `users` should be stored as a global variable, e.g. an attribute of some central object.

Here's an example of a database operation:

```python
# assuming we know user_id
user = await users.find(user_id)

async with user.command_maker() as fake_user:
    fake_user.name = name
    fake_user.items.add("Sword")

# now user is cached and all attributes are up to date
print(user.name)
print(user.items)

>>> Sponge123
>>> {'Stick', 'Compass', 'Sword'}
```

You might dislike 2 db requests in a row. In reality, the `find` request is usually just a `dict.get` call due to the document being cached. Don't worry about RAM though, you can specify cache lifetime in `NiceDocument.make_nice_collection`. The second call is done once we exit the `async with` statement. I named that var `fake_user` on purpose - it is actually a special object that pretends to be `user` but in reality it carefully stores and checks every change you propose inside the `async with` block. Once you exit this block, `fake_user` applies all changes to `user` and makes a **single** database request.


# Field functions

There're several field functions:

- `field`
- `field_with_list`
- `field_with_set`
- `field_with_dict`
- `nesting`
- `field_with_nestings`

They're very similar, except the last 2. Let's take a look at `field`:

## Arguments

- `default` - the default value of the field. `None` if unspecified.
- `from_raw` - a function that converts the value from mongo to something more suitable for you
- `to_raw` - the opposite of `from_raw`. If `from_raw` is specified, this must also be specified.
- `alias_for` - the original name of this field in mongo. This allows to harmlessly rename mongo fields in document wrappers.

In `field_with_list` or `field_with_set` converters like `[to]from_raw` are element-wise and are named `[to]from_raw_element`. In `field_with_dict` converters are item-wise. The default values are `[]`, `set()`, `{}` respectively, unless different default values are explicitly specified.


# Nestings

Of course in practice documents are a lot more complex and have multiple levels of nestings. This is why `core.py` is equipped with `NiceNestings`. In fact, `NiceDocument` is a subclass of it. Let's take a look at an example of `nesting` usage:

```python
class Player(NiceDocument):
    name: str = field()
    level: int = field(1)  # defaults to 1
    inventory: Inventory = nesting(Inventory)


class Inventory(NiceNesting):
    wood: int = field(0)
    iron: int = field(0)
    gold: int = field(0)
```

As you can see, `nesting` allows to wrap sub-dicts of dicts. They're as easy to manipulate as documents:

```python
player = await players.find(_id)

print(player.inventory.wood)

async with player.command_maker() as fake_player:
    fake_player.inventory.wood = 10

print(player.inventory.wood)

>>> 0
>>> 10
```


# Dicts with nestings

In the previous example we could avoid the nesting by moving wood, iron and gold to the `Player` structure. However, some nestings are unavoidable, namely nestings in sub-dicts. Let's have a look at an example:

```python
class Member(NiceNesting):
    xp: int = field(0)
    rating: int = field(0)


class Clan(NiceNesting):
    name: str = field()
    members: Dict[int, Member] = field_with_nestings(Member)


class Server(NiceDocument):
    clans: Dict[int, Clan] = field_with_nestings(Clan)
```

This example reveals the power of nestings. We've just created a basic system of clans with per-member statistics, taking only 8 lines of code.

Creating a clan would look like this:

```python
doc = await servers.find(server_id)

async with doc.command_maker() as fake_doc:
    fake_doc.clans[new_clan_id].name = "Cool Clan"
```

Note that we didn't explicitly add the clan, we specified one of its fields.

Adding a member:

```python
doc = await servers.find(server_id)
clan = doc.clans.get(clan_id)
# assuming clan is not Nnoe
async with clan.command_maker() as fake_clan:
    fake_clan.members[user_id].xp = 0
```

Deleting a clan:

```python
doc = await servers.find(server_id)

async with doc.command_maker() as fake_doc:
    fake_doc.clans.pop(clan_id)
```


# How do I usnet fields?

To unset a field using this interface you should set it to `...` (Ellipsis).

Example:

```python
user = await users.find(user_id)

async with user.command_maker() as fake_user:
    fake_user.items = ...
```

The only issue is that linters will complain about types. Unfortunarely this is a limitation of this ORM's interface.


# Which operations do fake_objects support

Currently only these operations are supported:

- `__setattr__`
- `__getattr__`
- `__setitem__`
- `__getitem__`
- `append`
- `add`
- `extend`
- `update`
- `remove`
- `pop`

I'm planning to add `__iadd__` soon.
