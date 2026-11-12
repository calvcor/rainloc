"""
Caché LRU acotada y thread-safe para matrices de modelos numéricos en memoria.

Cada matriz de la rejilla nacional (950x1500 float32) ocupa ~5,7 MB. Sin límite,
guardar cada paso de cada ciclo hace crecer la memoria del backend de forma
indefinida (varios GB al día). Las matrices expulsadas se vuelven a leer del
disco (.npz) cuando se necesitan.
"""
import os
import threading
from collections import OrderedDict
from typing import Any, Iterable, List, Optional

import numpy as np

DEFAULT_MAX_ARRAYS = int(os.getenv("MODEL_MEMORY_CACHE_MAX_ARRAYS", "96"))


class BoundedArrayCache:
    """Diccionario LRU con número máximo de entradas (compatible con el uso de dict en los workers)."""

    def __init__(self, max_items: int = DEFAULT_MAX_ARRAYS):
        self.max_items = max(1, max_items)
        self._data: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Optional[np.ndarray]:
        with self._lock:
            if key not in self._data:
                return default
            self._data.move_to_end(key)
            return self._data[key]

    def __getitem__(self, key: str) -> np.ndarray:
        with self._lock:
            value = self._data[key]
            self._data.move_to_end(key)
            return value

    def __setitem__(self, key: str, value: np.ndarray) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._data[key]

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def retain_prefixes(self, prefixes: Iterable[str]) -> int:
        """Elimina las entradas cuyas claves no empiezan por ninguno de los prefijos (ciclos vigentes)."""
        prefixes = tuple(prefixes)
        with self._lock:
            stale = [k for k in self._data if not k.startswith(prefixes)]
            for k in stale:
                del self._data[k]
            return len(stale)
