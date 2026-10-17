"""Observe actual classifier-cache returns and provider calls without hashing prompts."""
from contextlib import AbstractContextManager
import threading


class _Probe:
    def __init__(self, llm):
        self.llm = llm
        self.calls = 0

    def chat_json(self, *args, **kwargs):
        self.calls += 1
        return self.llm.chat_json(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.llm, name)


class ClassifierCacheCapture(AbstractContextManager):
    def __init__(self, *, legacy, structural_targets):
        self.legacy = legacy
        self.structural_targets = structural_targets
        self.originals = []
        self.lock = threading.RLock()
        self.counts = {'legacy': {'lookups': 0, 'hits': 0, 'misses': 0, 'errors': 0},
                       'structural': {'lookups': 0, 'hits': 0, 'misses': 0, 'disabled': 0,
                                      'fallbacks': 0, 'errors': 0, 'provider_calls': 0}}

    def _add(self, role, **values):
        with self.lock:
            for key, value in values.items():
                self.counts[role][key] += value

    def _legacy(self, original):
        def measured(*args, **kwargs):
            self._add('legacy', lookups=1)
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self._add('legacy', errors=1)
                raise
            self._add('legacy', **{'misses' if result is None else 'hits': 1})
            return result
        return measured

    def _structural(self, original):
        def measured(*args, **kwargs):
            args = list(args)
            kwargs = dict(kwargs)
            llm = args[3] if len(args) > 3 else kwargs.get('llm')
            if llm is None:
                self._add('structural', lookups=1, disabled=1)
                return original(*args, **kwargs)
            probe = _Probe(llm)
            if len(args) > 3:
                args[3] = probe
            else:
                kwargs['llm'] = probe
            self._add('structural', lookups=1)
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self._add('structural', errors=1, misses=1)
                raise
            else:
                cause = result.cause_code
                self._add('structural', **{'misses' if probe.calls else 'hits': 1},
                          fallbacks=int(cause is not None))
                return result
            finally:
                self._add('structural', provider_calls=probe.calls)
        return measured

    def result(self):
        with self.lock:
            return {role: dict(values) for role, values in self.counts.items()}

    def __enter__(self):
        wrappers = {}
        try:
            targets = [] if self.legacy is None else [(self.legacy, '_load_cached_classification', self._legacy)]
            targets += [(target, 'classify_table', self._structural) for target in self.structural_targets]
            for target, name, wrap in targets:
                if any(obj is target and key == name for obj, key, _ in self.originals):
                    continue
                original = getattr(target, name)
                self.originals.append((target, name, original))
                if id(original) not in wrappers:
                    wrappers[id(original)] = wrap(original)
                setattr(target, name, wrappers[id(original)])
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        for target, name, original in reversed(self.originals):
            setattr(target, name, original)
        return False
