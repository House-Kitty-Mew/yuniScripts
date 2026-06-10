import sys, types, unittest

def fixture(scope=None, autouse=False):
    def decorator(func):
        func._fixture = True
        func._autouse = autouse
        return func
    return decorator

def skip_if(condition, reason):
    if condition:
        raise unittest.SkipTest(reason)

def skip(reason):
    raise unittest.SkipTest(reason)

class _Mark:
    def __init__(self, name):
        self.name = name

class _MarkDecorator:
    def __init__(self, name):
        self.name = name
    def __call__(self, *args, **kwargs):
        return lambda func: func

# Create module
mod = types.ModuleType('pytest')
mod.fixture = fixture
mod.skip = skip
mod.skipif = skip_if
mod.mark = types.ModuleType('mark')
mod.mark.parametrize = lambda argnames, argvalues: lambda func: func
mod.mark.skip = _MarkDecorator('skip')
sys.modules['pytest'] = mod
sys.modules['pytest.fixture'] = fixture
