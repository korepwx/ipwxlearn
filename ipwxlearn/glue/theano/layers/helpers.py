# -*- coding: utf-8 -*-
import contextlib

import lasagne
import theano

from ipwxlearn import glue
from ipwxlearn.glue.theano.graph import current_graph
from ipwxlearn.glue.theano.scope import name_scope, current_name_scope
from ipwxlearn.glue.theano.utils import make_initializer
from ipwxlearn.utils.misc import require_object_name

__all__ = [
    'get_output'
]


class _Layer(lasagne.layers.Layer):

    _layer_name_validated_ = False

    @contextlib.contextmanager
    def _temporary_erase_name(self):
        """Temporarily erase the name of this layer."""
        old_name = self.name
        self.name = None
        yield
        self.name = old_name

    def add_param(self, spec, shape, name=None, **tags):
        # We expect the layer to have a qualified name.
        if not self._layer_name_validated_:
            if not self.name:
                raise ValueError('No name specified for the layer.')
            require_object_name(self.name)
            self._layer_name_validated_ = True

        # We don't add the parameter to the graph in the following situations:
        #
        # 1. The parameter does not have name.
        # 2. The 'spec' is already a Theano variable (which means the variable should have added to graph).
        if name is None or isinstance(spec, theano.Variable):
            return super(_Layer, self).add_param(spec, shape, name, **tags)

        # At this stage, we know that a new Theano variable should be created.
        # We call the backend method to construct the variable, and add to graph.
        require_object_name(name)
        with name_scope(self.name):
            full_name = current_name_scope().resolve_name(name)
            with self._temporary_erase_name():
                param = super(_Layer, self).add_param(spec, shape, full_name, **tags)
            for tag in self.params[param]:
                tags.setdefault(tag, True)
            init = make_initializer(spec, shape, dtype=glue.config.floatX)
            current_graph().add_variable(param, init, name=name, **tags)

        return param


def get_output(layer_or_layers, inputs=None):
    """
    Get the output tensor for given layer or layers.

    :param layer_or_layers: Layer or an iterable of layers.
    :param inputs: Dict with some input layers as keys and numeric scalars or numpy arrays as values,
                   causing these input layers to be substituted by constant values.

    :return: Output tensor, or a tuple of output tensor.
    """
    layer_or_layers = [layer_or_layers] \
        if isinstance(layer_or_layers, lasagne.layers.Layer) else list(layer_or_layers)
    return lasagne.layers.get_output(layer_or_layers, inputs=inputs)
