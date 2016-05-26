# -*- coding: utf-8 -*-
import threading


class ThreadLocalStack(threading.local):
    """Stack for keeping per-thread contexts."""

    def __init__(self, initial_values=None):
        self._stack = list(initial_values) if initial_values is not None else []

    def push(self, value):
        self._stack.append(value)

    def pop(self):
        return self._stack.pop()

    @property
    def top(self):
        return self._stack[-1]

    @property
    def empty(self):
        return not self._stack