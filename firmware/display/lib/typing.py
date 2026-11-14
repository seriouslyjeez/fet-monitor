# MicroPython stand-in (FeT Display): the names fetcore imports from typing. MicroPython never evaluates
# annotations, so they only have to exist.


class _Any:
    def __getitem__(self, item):
        return self


Any = Callable = Dict = Iterable = List = Optional = Sequence = Set = Tuple = Union = _Any()
