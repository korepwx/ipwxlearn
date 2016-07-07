# -*- coding: utf-8 -*-
import numpy as np

from ipwxlearn.glue import G
from ipwxlearn.utils.misc import maybe_iterable_to_list
from .base import BaseModel
from .constraints import ModelSupportDecoding

__all__ = [
    'MLP'
]


class MLP(G.layers.ChainLayer, BaseModel, ModelSupportDecoding):
    """
    Multi-layer perceptron model.

    A multi-layer perceptron consists of several fully-connected layers, with an optimal
    non-linear activation function.

    :param name: Name of this MLP model.
    :param incoming: Input layer, or the shape of input.
    :param layer_units: A tuple/list of integers, representing the number of units of each layer.
    :param nonlinearity: Non-linear activation function for each layer.
    :param dropout: A float number as the dropout probability for the output of each layer.
    :param W: Weight initializer, or a list of weight initializers, for the dense layers.
    :param b: Bias initializer, or a list of bias initializers, for the dense layers.
    """

    def __init__(self, name, incoming, layer_units, nonlinearity=G.nonlinearities.rectify, dropout=None,
                 W=G.init.XavierNormal(), b=G.init.Constant(0.0)):
        if not layer_units:
            raise ValueError('At least one layer should be specified.')

        # record the original arguments for the perceptron.
        self.layer_units = layer_units
        self.nonlinearity = nonlinearity
        self.dropout = dropout
        self.W = W
        self.b = b

        # If the weight and bias initializer is not a list, we copy the initializer
        # for as many times as the number of layers.
        W = maybe_iterable_to_list(W)
        b = maybe_iterable_to_list(b)

        if not isinstance(W, list):
            W = [W] * len(layer_units)
        else:
            assert(len(W) == len(layer_units))
        if not isinstance(b, list):
            b = [b] * len(layer_units)
        else:
            assert(len(b) == len(layer_units))

        # now create the MLP network.
        with G.name_scope(name):
            layers = []
            network = incoming
            for i, n_out in enumerate(layer_units):
                network = G.layers.DenseLayer('dense%d' % (i+1), network, num_units=n_out, nonlinearity=nonlinearity,
                                              W=W[i], b=b[i])
                layers.append(network)
                if dropout:
                    network = G.layers.DropoutLayer('dropout%d' % (i+1), network, p=dropout)
                    layers.append(network)

        super(MLP, self).__init__(children=layers, name=name)

    def get_decoder(self, name, tie_weights=False, nonlinearity=None, W=None, b=None, **kwargs):
        """
        Get the decoder model.

        :param name: Name for the decoder model.
        :param tie_weights: Set True if you wish to let the decoder network share weights
                            with the encoder network.
        :param nonlinearity: Specify new nonlinearity for the decoder.
                             If not specified, use the original model's nonlinearity.
        :param W: Specify new weight initializer for the decoder, if the weight is not tied.
                  If not specified, use the original model's initializer.
        :param b: Specify new bias initializer for the decoder.
                  If not specified, use the original model's initializer.

        :return: The decoder model.
        """
        # collect the weight and bias initializers.
        if tie_weights:
            W = [G.op.transpose(l.W) for l in reversed(self.children) if isinstance(l, G.layers.DenseLayer)]
        else:
            W = maybe_iterable_to_list(W or self.W)
            if isinstance(W, list):
                W = W[::-1]
        b = maybe_iterable_to_list(self.b)
        if isinstance(b, list):
            b = b[::-1]

        # compose the MLP network.
        layer_units = self.layer_units[-2::-1] + [np.prod(self.input_shapes[0][1:])]
        nonlinearity = nonlinearity or self.nonlinearity
        network = MLP(name=name, incoming=self, layer_units=layer_units, nonlinearity=nonlinearity, W=W, b=b)

        # reshape the output to original shape if necessary.

        return network
