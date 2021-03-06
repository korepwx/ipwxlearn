# -*- coding: utf-8 -*-
from __future__ import absolute_import

import tensorflow as tf

from ipwxlearn.utils import misc
from ipwxlearn.utils.misc import merged_context
from .scope import NameScope
from ..common.graph import BaseGraph, VariableTags, VariableInfo, current_graph, iter_graphs

__all__ = [
    'Graph',
    'VariableTags',
    'VariableInfo',
    'current_graph',
    'iter_graphs'
]


class Graph(BaseGraph):
    """Computation graph for TensorFlow backend."""

    def __init__(self):
        super(Graph, self).__init__()
        self.root_scope = NameScope(None)
        self._graph = tf.Graph()

        with self.tf_graph.as_default():
            tf.set_random_seed(self.initial_random_seed)

    def create_random_state(self, seed):
        return None

    @property
    def tf_graph(self):
        """Get the backend Graph object."""
        return self._graph

    @misc.contextmanager
    def as_default(self):
        with merged_context(super(Graph, self).as_default(), self._graph.as_default()):
            yield self
